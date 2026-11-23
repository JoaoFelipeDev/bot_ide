"""Mapeamento e reparo de sites de concorrentes com IA.

Fluxo (a IA so entra nos passos marcados com [IA]):

  1. Playwright abre o site, acha o campo de busca (heuristica; [IA] como plano B) e pesquisa um
     termo de teste. Da URL resultante sai o `search_url_template` (ou, se a URL nao muda, a
     receita guarda "digitar no campo X").
  2. O HTML da pagina de resultados e reduzido (html_trim) e enviado ao Claude [IA], que devolve
     os seletores CSS (item, titulo, preco, link, disponibilidade).
  3. A receita so e aceita se passar numa VALIDACAO: rodar o GenericCssScraper com ela e obter
     itens com titulo e preco legivel. Se falhar, o problema e devolvido ao Claude [IA] (ate
     MAX_SELECTOR_ATTEMPTS tentativas, com um teto total de chamadas por mapeamento).
  4. Aceita, a receita e salva em config/competitors.yaml (com backup da anterior). Dai em
     diante a coleta usa so CSS, sem gastar tokens.

`ensure_healthy` e o gatilho de auto-reparo: roda uma busca de teste com a receita atual; se
falhar, descobre se o site esta fora do ar (nao gasta tokens) ou se o layout mudou (repara).
"""
from __future__ import annotations

import json
import logging
import re
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any, Callable
from urllib.parse import quote, quote_plus, urlsplit, urlunsplit

from playwright.sync_api import Browser, Page, sync_playwright

from id_monitor import config
from id_monitor.ai.client import AIClientError, ClaudeClient, JsonCompleter
from id_monitor.ai.html_trim import trim_html
from id_monitor.ai.secrets import AIConfigError
from id_monitor.config import (
    CompetitorConfig,
    backup_competitor_recipe,
    get_competitor,
    update_competitor,
)
from id_monitor.scrapers.base import (
    USER_AGENTS,
    ScraperBlockedError,
    ScraperSearchError,
    human_scroll,
    new_stealth_context,
)
from id_monitor.scrapers.generic_css import GenericCssScraper, build_search_url

logger = logging.getLogger("id_monitor.ai.mapper")

Progress = Callable[[str], None]

# Termo de busca usado para mapear e para os testes de saude: tem resultados em qualquer
# livraria brasileira (e em editoras espiritas, o foco deste projeto).
PROBE_QUERY = "evangelho"
MAX_AI_CALLS = 5
MAX_SELECTOR_ATTEMPTS = 3
REPAIR_COOLDOWN = timedelta(hours=12)
# Quanto esperar pelos itens de resultado ao validar uma receita (uma receita errada espera
# esse tempo todo antes de ser reprovada, entao nao convem usar o timeout cheio de 15s).
VALIDATION_WAIT_MS = 8_000

_BLOCK_TITLES = ("just a moment", "attention required", "access denied", "verifying you are human")

_SELECTOR_SYSTEM_PROMPT = """You configure a CSS-selector based web scraper for Brazilian online bookstores.
You receive a simplified HTML of a SEARCH RESULTS page (scripts/styles removed; long repeated lists are collapsed to a few examples).
Return ONLY one JSON object, with no prose and no markdown, with these keys:
{
  "result_item_selector": CSS selector matching EACH product card in the results list (one element per product),
  "title_selector": CSS selector, relative to the card, for the element whose text is only the product title,
  "price_selector": CSS selector, relative to the card, for the element whose text is only the price the customer actually pays now,
  "link_selector": CSS selector, relative to the card, for the <a> that points to the product page,
  "availability_selector": CSS selector relative to the card for stock/availability text, or null if there is none
}
Rules:
- Selectors run in Chromium via querySelectorAll / element.querySelector. Standard CSS only (no :has-text, no XPath).
- Prefer stable hooks: ids, meaningful class names, data-* attributes, itemprop. AVOID auto-generated or hashed class names (css-1a2b3c, sc-xyz, _a1b2c3), nth-child/nth-of-type and long absolute paths.
- result_item_selector must match individual product cards, not the whole list container, and should avoid cards in "related"/"recommended" carousels, headers and footers when possible.
- If a card shows an old crossed-out price and also a current/promotional/"a vista" price, price_selector must point to the CURRENT price element only, never to a container that holds both prices.
- You may join a few alternatives with commas if the site uses more than one card layout.
- If the page is not a product search results page (404, empty search, captcha, etc.), return {"error": "short reason in Portuguese"} instead.
"""

_SEARCH_INPUT_SYSTEM_PROMPT = """You find the site search box of a Brazilian online store.
You receive the simplified HTML of its home page. Return ONLY one JSON object:
{"search_input_selector": "<CSS selector of the visible text <input> used to search products>"}
Prefer an id or a name attribute. If there is no search box, return {"error": "short reason in Portuguese"}.
"""

_FIND_SEARCH_INPUTS_JS = """
() => {
  const visible = el => {
    const r = el.getBoundingClientRect();
    const s = getComputedStyle(el);
    return r.width > 40 && r.height > 10 && s.visibility !== 'hidden' && s.display !== 'none';
  };
  const hint = /busc|pesquis|search|procur|termo|query|keyword|\\bq\\b/i;
  const esc = v => String(v).replace(/"/g, '\\\\"');
  const out = [];
  document.querySelectorAll('input').forEach(el => {
    const type = (el.getAttribute('type') || 'text').toLowerCase();
    if (type !== 'text' && type !== 'search') return;
    const isVisible = visible(el);
    // Campo escondido (ex.: barra de busca que so aparece num menu responsivo) so serve se
    // estiver dentro de um <form>, pois dai da para enviar o formulario por JS.
    if (!isVisible && !el.closest('form')) return;
    let score = isVisible ? 10 : 0;
    if (type === 'search') score += 3;
    if (hint.test([el.name, el.id, el.placeholder, el.getAttribute('aria-label'), el.className].join(' '))) score += 2;
    const form = el.closest('form');
    if (form && hint.test([form.getAttribute('action'), form.getAttribute('role'), form.id, form.className].join(' '))) score += 2;
    if (el.closest('header, [role=banner]')) score += 1;
    let selector;
    if (el.id) selector = '#' + CSS.escape(el.id);
    else if (el.name) selector = 'input[name="' + esc(el.name) + '"]';
    else if (el.placeholder) selector = 'input[placeholder="' + esc(el.placeholder) + '"]';
    else selector = 'input[type="' + type + '"]';
    out.push({selector, score, visible: isVisible});
  });
  const relevant = out.filter(c => (c.score - (c.visible ? 10 : 0)) > 0 || c.visible);
  relevant.sort((a, b) => b.score - a.score);
  const seen = new Set();
  return relevant.filter(c => !seen.has(c.selector) && seen.add(c.selector)).slice(0, 4);
}
"""

# Preenche um campo (mesmo escondido) e envia o formulario dele, como um usuario faria.
_SUBMIT_FORM_JS = """
(el, value) => {
  el.value = value;
  el.dispatchEvent(new Event('input', {bubbles: true}));
  const form = el.form;
  if (!form) return false;
  if (form.requestSubmit) form.requestSubmit(); else form.submit();
  return true;
}
"""


class MappingError(Exception):
    """Falha ao mapear o site (sem campo de busca, protecao anti-robo, receita rejeitada...)."""


class SiteDownError(MappingError):
    """O site (ou a pagina de busca dele) nao respondeu - nao adianta gastar tokens."""


@dataclass
class MapResult:
    success: bool
    state: str  # "mapped" | "repaired" | "down" | "failed"
    message: str
    cfg: CompetitorConfig | None = None
    ai_calls: int = 0


@dataclass
class HealthOutcome:
    cfg: CompetitorConfig
    state: str  # "ok" | "mapped" | "repaired" | "down" | "blocked" | "failed" | "skipped"
    message: str

    @property
    def usable(self) -> bool:
        return self.state in ("ok", "mapped", "repaired")


@dataclass
class ValidationReport:
    ok: bool
    count: int = 0
    with_price: int = 0
    samples: list[tuple[str, float | None]] = field(default_factory=list)
    error: str | None = None
    blocked: bool = False  # o site bloqueou o robo: nao e problema de receita, nao chamar a IA

    def describe(self) -> str:
        if self.error:
            return self.error
        if self.count == 0:
            return "A busca de teste retornou 0 itens: o seletor do item de resultado nao encontrou nada."
        text = f"{self.count} itens encontrados, mas so {self.with_price} com preco legivel."
        if self.samples:
            shown = "; ".join(f"{title[:50]!r} -> preco {price}" for title, price in self.samples)
            text += f" Exemplos: {shown}."
        return text + " Revise price_selector."


@dataclass
class _SearchStrategy:
    mode: str  # "url" | "type"
    url_template: str | None = None
    input_selector: str | None = None


class _Budget:
    def __init__(self, total: int) -> None:
        self.total = total
        self.used = 0

    @property
    def exhausted(self) -> bool:
        return self.used >= self.total

    def spend(self) -> None:
        self.used += 1


# ----------------------------------------------------------------------------- utilitarios
def _short(exc: Exception) -> str:
    return str(exc).strip().splitlines()[0][:200] if str(exc).strip() else exc.__class__.__name__


def check_site_reachable(url: str, timeout: float = 12.0) -> tuple[bool, str]:
    """Checagem barata (sem navegador) se o servidor responde. Qualquer resposta HTTP, mesmo
    um 403/404, conta como "no ar"; so falha de conexao/DNS/timeout conta como fora do ar."""
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENTS[0]})
    try:
        with urllib.request.urlopen(request, timeout=timeout):  # noqa: S310 - URL vem da config
            return True, "ok"
    except urllib.error.HTTPError as exc:
        return True, f"HTTP {exc.code}"
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        return False, str(getattr(exc, "reason", exc))


def _goto(page: Page, url: str, timeout_ms: int = 30_000) -> None:
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    except Exception as exc:  # noqa: BLE001 - qualquer falha de navegacao = site inacessivel
        raise SiteDownError(f"Nao consegui abrir {url}: {_short(exc)}") from exc


def _settle(page: Page) -> None:
    """Espera a pagina assentar (JS/lazy-load) e levanta erro se houver protecao anti-robo."""
    try:
        page.wait_for_load_state("networkidle", timeout=8_000)
    except Exception:  # noqa: BLE001 - sites com polling nunca ficam "idle"; segue em frente
        pass
    human_scroll(page, steps=3)
    try:
        page.wait_for_timeout(800)
    except Exception:  # noqa: BLE001
        pass
    title = (page.title() or "").lower()
    if any(block in title for block in _BLOCK_TITLES):
        raise MappingError(
            "O site mostrou uma protecao anti-robo (Cloudflare ou similar) e nao deixou a busca automatica."
        )


def _url_to_template(url: str, probe: str) -> str | None:
    """Troca o termo de busca (na path/query da URL) por {query}. None se ele nao aparece."""
    parts = urlsplit(url)
    variants = sorted({quote_plus(probe), quote(probe), probe}, key=len, reverse=True)

    def swap(text: str) -> tuple[str, bool]:
        text = text.replace("{", "{{").replace("}", "}}")
        found = False
        for variant in variants:
            pattern = re.compile(re.escape(variant), re.IGNORECASE)
            if pattern.search(text):
                text = pattern.sub("{query}", text)
                found = True
        return text, found

    path, found_path = swap(parts.path)
    query, found_query = swap(parts.query)
    if not (found_path or found_query):
        return None
    # No path o espaco vira %20 (e nao '+'): usamos o marcador {query_path} (ver build_search_url).
    path = path.replace("{query}", "{query_path}")
    return urlunsplit((parts.scheme, parts.netloc, path, query, ""))


def _opt_str(value: Any) -> str | None:
    """String nao vazia ou None (a IA as vezes devolve null, "" ou o texto "null")."""
    if isinstance(value, str) and value.strip() and value.strip().lower() != "null":
        return value.strip()
    return None


def _recipe_dict(cfg: CompetitorConfig) -> dict[str, Any]:
    return {
        "result_item_selector": cfg.result_item_selector,
        "title_selector": cfg.title_selector,
        "price_selector": cfg.price_selector,
        "link_selector": cfg.link_selector,
        "availability_selector": cfg.availability_selector,
    }


# ------------------------------------------------------------------------ validacao
def validate_recipe(browser: Browser, cfg: CompetitorConfig, query: str = PROBE_QUERY) -> ValidationReport:
    """Roda o scraper generico com a receita de `cfg` numa busca de teste e diz se ela presta:
    precisa trazer itens com titulo e, na maioria, preco legivel."""
    context = new_stealth_context(browser)
    try:
        page = context.new_page()
        try:
            results = GenericCssScraper(cfg).search_once(page, query, wait_timeout_ms=VALIDATION_WAIT_MS)
        except ScraperBlockedError as exc:
            return ValidationReport(ok=False, error=str(exc), blocked=True)
        except ScraperSearchError as exc:
            return ValidationReport(ok=False, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - seletor invalido etc. viram feedback para a IA
            return ValidationReport(ok=False, error=f"Erro ao aplicar a receita: {_short(exc)}")
    finally:
        context.close()

    return evaluate_results(results)


def evaluate_results(results: list) -> ValidationReport:
    count = len(results)
    with_price = sum(1 for r in results if r.price is not None)
    samples = [(r.title, r.price) for r in results[:3]]
    ok = count > 0 and with_price >= max(1, count // 2)
    return ValidationReport(ok=ok, count=count, with_price=with_price, samples=samples)


# ------------------------------------------------------------------------ passos com IA
def _ask_selectors(
    client: JsonCompleter,
    *,
    cfg: CompetitorConfig,
    page: Page,
    probe: str,
    previous: dict[str, Any] | None,
    problem: str | None,
) -> dict[str, Any]:
    html = trim_html(page.content(), max_chars=24_000, drop_layout=True, focus_prices=True)
    lines = [
        f"Site: {cfg.name} ({cfg.base_url})",
        f'Termo buscado: "{probe}"',
        f"URL da pagina de resultados: {page.url}",
        f"Titulo da pagina: {page.title()}",
    ]
    if previous and problem:
        lines.append(f"Tentativa anterior (REJEITADA): {json.dumps(previous, ensure_ascii=False)}")
        lines.append(f"Problema detectado ao testar: {problem}")
        lines.append("Corrija os seletores.")
    elif problem:
        lines.append(f"Contexto: a receita antiga parou de funcionar ({problem}).")
    lines.append("\nHTML simplificado:\n" + html)

    answer = client.complete_json(_SELECTOR_SYSTEM_PROMPT, "\n".join(lines), max_tokens=1024)
    if answer.get("error"):
        raise MappingError(f"A IA nao viu resultados de busca nesta pagina: {answer['error']}")
    for key in ("result_item_selector", "title_selector", "price_selector"):
        if not isinstance(answer.get(key), str) or not answer[key].strip():
            raise MappingError(f"A resposta da IA nao trouxe '{key}'.")
    return answer


def _ask_search_input(client: JsonCompleter, page: Page) -> str:
    html = trim_html(page.content(), max_chars=14_000, drop_layout=False)
    answer = client.complete_json(_SEARCH_INPUT_SYSTEM_PROMPT, f"URL: {page.url}\n\nHTML:\n{html}", max_tokens=256)
    selector = answer.get("search_input_selector")
    if answer.get("error") or not isinstance(selector, str) or not selector.strip():
        raise MappingError(f"A IA nao achou o campo de busca: {answer.get('error', 'sem resposta')}")
    return selector.strip()


# ---------------------------------------------------------------- descoberta da busca
def _discover_search(
    page: Page,
    cfg: CompetitorConfig,
    probe: str,
    client: JsonCompleter,
    budget: _Budget,
    progress: Progress,
) -> _SearchStrategy:
    """Acha o campo de busca na home, pesquisa `probe` e descobre como a busca funciona.
    Ao terminar, `page` esta na pagina de resultados."""
    _goto(page, cfg.base_url)
    _settle(page)

    candidates: list[dict[str, Any]] = list(page.evaluate(_FIND_SEARCH_INPUTS_JS))
    if not candidates and not budget.exhausted:
        progress("Nao achei o campo de busca pela heuristica; pedindo ajuda a IA...")
        budget.spend()
        candidates = [{"selector": _ask_search_input(client, page), "visible": True}]
    if not candidates:
        raise MappingError("Nao encontrei o campo de busca do site.")

    def submit(candidate: dict[str, Any]) -> bool:
        """Faz a busca de `probe` por esse campo. Retorna False se o campo nao funcionou."""
        selector = candidate["selector"]
        try:
            if candidate.get("visible", True):
                # fill (e nao click+type): pop-ups de marketing costumam interceptar o clique.
                box = page.locator(f"{selector} >> visible=true").first
                box.fill(probe, timeout=5_000)
                box.press("Enter")
            else:
                if not page.eval_on_selector(selector, _SUBMIT_FORM_JS, probe):
                    return False
        except Exception as exc:  # noqa: BLE001 - tenta o proximo candidato
            logger.info("Campo %s nao funcionou (%s).", selector, _short(exc))
            return False
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15_000)
        except Exception:  # noqa: BLE001
            pass
        _settle(page)
        return True

    fallback_type: _SearchStrategy | None = None
    for candidate in candidates:
        _goto(page, cfg.base_url)
        _settle(page)
        start_url = page.url
        if not submit(candidate):
            continue

        if page.url != start_url:
            template = _url_to_template(page.url, probe)
            if template:
                progress(f"Busca por URL descoberta: {template}")
                return _SearchStrategy("url", url_template=template)
        if fallback_type is None and candidate.get("visible", True):
            # Guarda o primeiro campo "digitavel" e segue testando os demais: um deles pode
            # navegar para uma URL de busca (preferivel, e mais robusta que digitar).
            fallback_type = _SearchStrategy("type", input_selector=candidate["selector"])

    if fallback_type is not None:
        progress(f"A busca nao muda a URL; vou digitar no campo {fallback_type.input_selector}.")
        # Refaz a busca com o campo escolhido para deixar `page` na pagina de resultados.
        _goto(page, cfg.base_url)
        _settle(page)
        if submit({"selector": fallback_type.input_selector, "visible": True}):
            return fallback_type

    raise MappingError("Nao consegui usar nenhum campo de busca do site.")


# ----------------------------------------------------------------- ajuste de seletores
def _fit_selectors(
    browser: Browser,
    page: Page,
    cfg: CompetitorConfig,
    strategy: _SearchStrategy,
    client: JsonCompleter,
    budget: _Budget,
    progress: Progress,
    problem: str | None,
) -> MapResult:
    previous: dict[str, Any] | None = None
    last_message = problem or "Nao foi possivel validar uma receita."

    for attempt in range(1, MAX_SELECTOR_ATTEMPTS + 1):
        if budget.exhausted:
            break
        progress(f"Pedindo seletores a IA (tentativa {attempt}/{MAX_SELECTOR_ATTEMPTS})...")
        budget.spend()
        try:
            answer = _ask_selectors(client, cfg=cfg, page=page, probe=PROBE_QUERY, previous=previous, problem=problem)
        except MappingError as exc:
            return MapResult(False, "failed", str(exc), ai_calls=budget.used)

        candidate = replace(
            cfg,
            search_mode=strategy.mode,
            search_url_template=strategy.url_template,
            search_input_selector=strategy.input_selector,
            result_item_selector=answer["result_item_selector"].strip(),
            title_selector=answer["title_selector"].strip(),
            price_selector=answer["price_selector"].strip(),
            link_selector=_opt_str(answer.get("link_selector")) or "a",
            availability_selector=_opt_str(answer.get("availability_selector")),
        )

        progress("Validando a receita com uma busca de teste...")
        report = validate_recipe(browser, candidate)
        if report.blocked:
            return MapResult(False, "blocked", report.describe(), ai_calls=budget.used)
        if report.ok:
            msg = f"Receita validada: {report.count} itens, {report.with_price} com preco."
            progress(msg)
            return MapResult(True, "mapped", msg, cfg=candidate, ai_calls=budget.used)

        last_message = report.describe()
        progress(f"Receita rejeitada: {last_message}")
        previous = _recipe_dict(candidate)
        problem = last_message

    return MapResult(False, "failed", last_message, ai_calls=budget.used)


# --------------------------------------------------------------------- orquestracao
def _map_with_browser(
    browser: Browser,
    cfg: CompetitorConfig,
    client: JsonCompleter,
    progress: Progress,
    repair_reason: str | None,
) -> MapResult:
    budget = _Budget(MAX_AI_CALLS)
    context = new_stealth_context(browser)
    try:
        page = context.new_page()
        order: list[str] = []
        if cfg.search_mode == "url" and cfg.search_url_template:
            order.append("existing")
        order.append("discover")

        problem = repair_reason
        last: MapResult | None = None
        for kind in order:
            try:
                if kind == "existing":
                    progress("Tentando o endereco de busca que ja estava configurado...")
                    _goto(page, build_search_url(cfg.search_url_template, PROBE_QUERY))
                    _settle(page)
                    strategy = _SearchStrategy("url", url_template=cfg.search_url_template)
                else:
                    progress("Procurando o campo de busca do site...")
                    strategy = _discover_search(page, cfg, PROBE_QUERY, client, budget, progress)
                last = _fit_selectors(browser, page, cfg, strategy, client, budget, progress, problem)
            except SiteDownError:
                raise
            except MappingError as exc:
                last = MapResult(False, "failed", str(exc), ai_calls=budget.used)

            if last.success or last.state == "blocked":
                return last
            problem = last.message
            if budget.exhausted:
                break
        return last or MapResult(False, "failed", "Nao foi possivel mapear o site.", ai_calls=budget.used)
    except SiteDownError as exc:
        return MapResult(False, "down", str(exc), ai_calls=budget.used)
    finally:
        context.close()


def _save_mapping(key: str, result: MapResult) -> CompetitorConfig:
    assert result.cfg is not None
    backup_competitor_recipe(key)
    cfg = result.cfg
    update_competitor(
        key,
        {
            "search_mode": cfg.search_mode,
            "search_input_selector": cfg.search_input_selector,
            "search_url_template": cfg.search_url_template,
            "result_item_selector": cfg.result_item_selector,
            "title_selector": cfg.title_selector,
            "price_selector": cfg.price_selector,
            "link_selector": cfg.link_selector,
            "availability_selector": cfg.availability_selector,
            "mapped_by_ai": True,
            "mapped_at": datetime.now().isoformat(timespec="seconds"),
            "status": "ok",
        },
    )
    return get_competitor(key)


def map_competitor(
    key: str,
    *,
    client: JsonCompleter | None = None,
    browser: Browser | None = None,
    progress: Progress | None = None,
    repair_reason: str | None = None,
    success_state: str = "mapped",
) -> MapResult:
    """Mapeia (ou re-mapeia) um concorrente com a IA e, se a receita passar na validacao, salva
    em competitors.yaml (com backup da anterior). Levanta AIConfigError/AIClientError se a IA
    nao puder ser usada (sem chave, chave invalida, sem credito...)."""
    progress = progress or logger.info
    cfg = get_competitor(key)
    ai = client or ClaudeClient()

    progress(f"Mapeando '{cfg.name}' ({cfg.base_url})...")
    if browser is not None:
        result = _map_with_browser(browser, cfg, ai, progress, repair_reason)
    else:
        with sync_playwright() as playwright:
            own_browser = playwright.chromium.launch(headless=True)
            try:
                result = _map_with_browser(own_browser, cfg, ai, progress, repair_reason)
            finally:
                own_browser.close()

    if result.success:
        saved = _save_mapping(key, result)
        result = replace(result, state=success_state, cfg=saved)
        _clear_failure(key)
        progress(f"'{cfg.name}' mapeado e salvo ({result.ai_calls} chamada(s) a IA).")
    elif result.state == "down":
        if cfg.status != "fora_do_ar":
            update_competitor(key, {"status": "fora_do_ar"})
        progress(f"'{cfg.name}' parece fora do ar: {result.message}")
    else:
        progress(f"Nao consegui mapear '{cfg.name}': {result.message}")
    return result


def map_competitors(
    keys: list[str],
    *,
    client: JsonCompleter | None = None,
    progress: Progress | None = None,
) -> list[tuple[str, MapResult]]:
    """Mapeia varios concorrentes reaproveitando o mesmo navegador e o mesmo cliente de IA.
    Falhas de um site nao interrompem os outros; erros da IA (chave invalida, sem credito)
    interrompem tudo, pois afetariam todos os sites."""
    progress = progress or logger.info
    ai = client or ClaudeClient()
    results: list[tuple[str, MapResult]] = []
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for key in keys:
                results.append(
                    (key, map_competitor(key, client=ai, browser=browser, progress=progress))
                )
        finally:
            browser.close()
    return results


# ------------------------------------------------------------- auto-reparo (gatilho)
def _state_path():
    return config.DATA_DIR / "ai_state.json"


def _load_state() -> dict[str, str]:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _record_failure(key: str) -> None:
    state = _load_state()
    state[key] = datetime.now().isoformat(timespec="seconds")
    config.ensure_directories()
    _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def _clear_failure(key: str) -> None:
    state = _load_state()
    if key in state:
        del state[key]
        _state_path().write_text(json.dumps(state, indent=2), encoding="utf-8")


def _in_cooldown(key: str) -> bool:
    raw = _load_state().get(key)
    if not raw:
        return False
    try:
        return datetime.now() - datetime.fromisoformat(raw) < REPAIR_COOLDOWN
    except ValueError:
        return False


def _looks_down(report: ValidationReport) -> bool:
    return bool(report.error and "Falha ao abrir" in report.error)


def _set_status(cfg: CompetitorConfig, status: str) -> CompetitorConfig:
    if cfg.status != status:
        update_competitor(cfg.key, {"status": status})
        return get_competitor(cfg.key)
    return cfg


def ensure_healthy(
    cfg: CompetitorConfig,
    browser: Browser,
    *,
    client: JsonCompleter | None = None,
    progress: Progress | None = None,
) -> HealthOutcome:
    """Garante que `cfg` tem uma receita funcionando, chamando a IA so se for preciso.

    - sem receita -> mapeia;
    - receita OK numa busca de teste -> segue, zero tokens;
    - receita falhou e o site nao responde -> marca "fora do ar", zero tokens;
    - receita falhou com o site no ar -> repara com IA (no maximo uma vez por 12h por
      concorrente se o reparo falhar, para nao gastar tokens a cada coleta).
    """
    progress = progress or logger.info

    def run_map(reason: str | None, success_state: str) -> HealthOutcome:
        if _in_cooldown(cfg.key):
            return HealthOutcome(
                cfg, "skipped",
                "Uma tentativa recente de mapear/reparar este site falhou; nova tentativa so apos o intervalo de espera.",
            )
        try:
            result = map_competitor(
                cfg.key, client=client, browser=browser, progress=progress,
                repair_reason=reason, success_state=success_state,
            )
        except (AIConfigError, AIClientError) as exc:
            return HealthOutcome(cfg, "failed", f"IA indisponivel: {exc}")
        if result.state == "failed":
            _record_failure(cfg.key)
        return HealthOutcome(result.cfg or get_competitor(cfg.key), result.state, result.message)

    if not cfg.has_recipe:
        progress(f"[{cfg.name}] sem receita de busca; mapeando com IA...")
        return run_map(None, "mapped")

    report = validate_recipe(browser, cfg)
    if not report.ok and _looks_down(report):
        time.sleep(3)  # uma segunda chance antes de concluir que o site caiu
        report = validate_recipe(browser, cfg)

    if report.ok:
        if cfg.status in ("fora_do_ar", "precisa_mapear"):
            cfg = _set_status(cfg, "ok")
        return HealthOutcome(cfg, "ok", f"Receita OK ({report.count} resultado(s) no teste).")

    if report.blocked:
        # O site esta no ar mas barrou o robo: a receita pode estar certa. Nao gasta tokens.
        return HealthOutcome(cfg, "blocked", report.describe())

    if _looks_down(report):
        site_down, detail = True, report.error or ""
    else:
        reachable, detail = check_site_reachable(cfg.base_url)
        site_down = not reachable
    if site_down:
        cfg = _set_status(cfg, "fora_do_ar")
        return HealthOutcome(cfg, "down", f"Site fora do ar ou inacessivel: {detail}")

    progress(f"[{cfg.name}] a receita parou de funcionar ({report.describe()}); reparando com IA...")
    return run_map(report.describe(), "repaired")
