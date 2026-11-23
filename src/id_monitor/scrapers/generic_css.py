"""Scraper generico, configurado via CSS selectors definidos em config/competitors.yaml.

Serve de implementacao padrao para a maioria dos concorrentes: navega para a URL de busca,
espera a lista de resultados aparecer e extrai titulo/preco/link de cada item encontrado.

Sites com HTML fora do padrao, busca que exige clique (nao aceita URL direta) ou paginacao
via JS devem sobrescrever `search()` numa subclasse propria (ver scrapers/<nome>.py) em vez de
usar esta classe diretamente.
"""
from __future__ import annotations

from urllib.parse import quote, quote_plus, urljoin

from playwright.sync_api import Page

from id_monitor.scrapers.base import (
    CompetitorScraper,
    RawResult,
    ScraperBlockedError,
    ScraperNotConfiguredError,
    ScraperSearchError,
    detect_block,
    human_delay,
    human_scroll,
    parse_price,
    retry_on_failure,
)

DEFAULT_TIMEOUT_MS = 15_000


def build_search_url(template: str, query: str) -> str:
    """Monta a URL de busca a partir do template. {query} vai codificado para query string
    (espaco -> '+'); {query_path} vai codificado para path de URL (espaco -> '%20'), usado
    quando o site busca por /<termo> em vez de ?q=<termo>."""
    return template.format(query=quote_plus(query), query_path=quote(query, safe=""))


class GenericCssScraper(CompetitorScraper):
    """Busca por URL de template + extracao de resultados via seletores CSS configuraveis."""

    @retry_on_failure(max_attempts=3, base_delay=2.0)
    def search(self, page: Page, query: str) -> list[RawResult]:
        return self.search_once(page, query)

    def search_once(self, page: Page, query: str, wait_timeout_ms: int | None = None) -> list[RawResult]:
        """Uma unica tentativa de busca (sem retry). Usada diretamente pela validacao de
        receitas do mapeamento por IA, que nao quer esperar os backoffs do retry.
        `wait_timeout_ms` limita a espera pelos itens de resultado (padrao: DEFAULT_TIMEOUT_MS)."""
        cfg = self.competitor_cfg
        if not cfg.has_recipe:
            raise ScraperNotConfiguredError(
                f"Concorrente '{cfg.key}' nao tem receita de busca (URL/campo de busca + seletores) "
                "configurada. Use 'Mapear com IA' ou edite os seletores."
            )

        if cfg.search_mode == "type":
            self._submit_search_by_typing(page, query)
        else:
            search_url = build_search_url(cfg.search_url_template, query)
            try:
                page.goto(search_url, timeout=DEFAULT_TIMEOUT_MS, wait_until="domcontentloaded")
            except Exception as exc:  # noqa: BLE001 - qualquer falha de navegacao e tratada igual
                raise ScraperSearchError(f"Falha ao abrir {search_url}: {exc}") from exc

        human_delay()

        try:
            page.wait_for_selector(cfg.result_item_selector, timeout=wait_timeout_ms or DEFAULT_TIMEOUT_MS)
        except Exception:
            # Pagina carregou mas nenhum resultado apareceu dentro do timeout: tratamos como
            # "nenhum resultado encontrado" em vez de erro, pois pode ser so uma busca vazia...
            # a menos que a pagina seja um bloqueio (firewall/captcha), que NAO e "nao achei".
            self._raise_if_blocked(page)
            return []

        human_scroll(page)

        items = page.query_selector_all(cfg.result_item_selector)
        results: list[RawResult] = []

        for item in items:
            title = self._extract_text(item, cfg.title_selector)
            if not title:
                continue

            price_text = self._extract_text(item, cfg.price_selector)
            price = parse_price(price_text)

            link = None
            if cfg.link_selector:
                link_el = item.query_selector(cfg.link_selector)
                if link_el is not None:
                    link = link_el.get_attribute("href")
                    if link and not link.startswith(("http://", "https://", "#", "javascript:")):
                        link = urljoin(page.url, link)

            availability = (
                self._extract_text(item, cfg.availability_selector)
                if cfg.availability_selector
                else None
            )

            results.append(
                RawResult(
                    title=title.strip(),
                    price=price,
                    availability=availability,
                    url=link,
                )
            )

        return results

    def _submit_search_by_typing(self, page: Page, query: str) -> None:
        """Modo "type": abre a home, digita `query` no campo de busca e aperta Enter (para sites
        cuja busca nao gera uma URL previsivel)."""
        cfg = self.competitor_cfg
        try:
            page.goto(cfg.base_url, timeout=DEFAULT_TIMEOUT_MS, wait_until="domcontentloaded")
        except Exception as exc:  # noqa: BLE001
            raise ScraperSearchError(f"Falha ao abrir {cfg.base_url}: {exc}") from exc

        try:
            box = page.locator(f"{cfg.search_input_selector} >> visible=true").first
            # fill (e nao click+type): pop-ups de marketing costumam interceptar o clique.
            box.fill(query, timeout=DEFAULT_TIMEOUT_MS)
            box.press("Enter")
        except Exception as exc:  # noqa: BLE001
            raise ScraperSearchError(
                f"Nao consegui usar o campo de busca '{cfg.search_input_selector}': {exc}"
            ) from exc

    @staticmethod
    def _raise_if_blocked(page: Page) -> None:
        try:
            title = page.title()
            body = page.inner_text("body", timeout=3000)
        except Exception:  # noqa: BLE001 - se nem da para ler a pagina, segue como "sem resultados"
            return
        marker = detect_block(title, body)
        if marker:
            raise ScraperBlockedError(
                f"O site bloqueou o acesso automatico ('{marker}'). Costuma ser temporario: "
                "espere um tempo (ou use outra rede) e colete de novo."
            )

    @staticmethod
    def _extract_text(item, selector: str | None) -> str | None:
        if not selector:
            return None
        el = item.query_selector(selector)
        if el is None:
            return None
        return el.inner_text()
