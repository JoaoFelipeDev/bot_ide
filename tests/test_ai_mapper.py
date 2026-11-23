"""Testes do mapeamento/reparo por IA, com um cliente de IA falso (nenhuma chamada real a API)
e o concorrente fake local (home com campo de busca + pagina de resultados)."""
from __future__ import annotations

import http.server
import socket
import threading
import types
from functools import partial
from pathlib import Path

import pytest
from playwright.sync_api import sync_playwright

from id_monitor import config as config_module
from id_monitor.ai import mapper as mapper_module
from id_monitor.ai.mapper import (
    HealthOutcome,
    MappingError,
    ensure_healthy,
    evaluate_results,
    map_competitor,
    map_competitors,
)
from id_monitor.config import CompetitorConfig
from id_monitor.scrapers.base import RawResult, new_stealth_context
from id_monitor.scrapers.generic_css import GenericCssScraper, build_search_url

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "fake_competitor"

GOOD_ANSWER = {
    "result_item_selector": "li.product",
    "title_selector": "h2.title",
    "price_selector": "span.price",
    "link_selector": "a",
    "availability_selector": None,
}
BAD_ANSWER = {**GOOD_ANSWER, "result_item_selector": "li.nao-existe"}


@pytest.fixture(autouse=True)
def fast_browser_waits(monkeypatch):
    """Tira as pausas "humanas" e encurta os timeouts: contra o servidor local nao ha
    anti-robo para enganar, e uma receita errada nao precisa esperar 8s para ser reprovada."""
    from id_monitor.scrapers import generic_css

    monkeypatch.setattr(generic_css, "human_delay", lambda *a, **k: None)
    monkeypatch.setattr(generic_css, "human_scroll", lambda *a, **k: None)
    monkeypatch.setattr(mapper_module, "human_scroll", lambda *a, **k: None)
    monkeypatch.setattr(mapper_module, "VALIDATION_WAIT_MS", 1_500)


class FakeAI:
    """Devolve respostas pre-definidas (uma por chamada; a ultima se repete) e guarda os prompts."""

    def __init__(self, *answers: dict) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    @property
    def calls(self) -> int:
        return len(self.prompts)

    def complete_json(self, system: str, user: str, max_tokens: int = 1024) -> dict:
        self.prompts.append(user)
        index = min(len(self.prompts) - 1, len(self.answers) - 1)
        return self.answers[index]


class ForbiddenAI:
    def complete_json(self, *args, **kwargs):  # pragma: no cover - so roda se o teste falhar
        raise AssertionError("A IA nao deveria ter sido chamada")


@pytest.fixture(scope="module")
def local_server():
    handler_cls = partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURE_DIR))
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server.server_address[1]
    server.shutdown()
    thread.join(timeout=2)


@pytest.fixture
def browser():
    # Escopo de funcao (e nao de modulo): alguns testes chamam funcoes que abrem o proprio
    # sync_playwright(), e dois sync_playwright ativos na mesma thread nao sao permitidos.
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch(headless=True)
        yield instance
        instance.close()


@pytest.fixture
def project(tmp_path, monkeypatch, local_server):
    """Config temporaria com 1 concorrente 'fake' (sem receita) apontando para o servidor local."""
    config_dir, data_dir = tmp_path / "config", tmp_path / "data"
    config_dir.mkdir()
    data_dir.mkdir()
    monkeypatch.setattr(config_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "COMPETITORS_CONFIG_PATH", config_dir / "competitors.yaml")
    # nenhum teste deve esperar de verdade os "3 segundos de segunda chance"
    monkeypatch.setattr(mapper_module, "time", types.SimpleNamespace(sleep=lambda _s: None))

    def write(extra: str = "", base_url: str | None = None) -> None:
        url = base_url or f"http://127.0.0.1:{local_server}"
        (config_dir / "competitors.yaml").write_text(
            "competitors:\n"
            "  - key: fake\n"
            "    name: Fake\n"
            f"    base_url: {url}\n"
            "    module: id_monitor.scrapers.generic_css\n"
            "    class_name: GenericCssScraper\n"
            "    active: true\n"
            "    status: precisa_mapear\n" + extra,
            encoding="utf-8",
        )

    write()
    return types.SimpleNamespace(write=write, port=local_server)


def working_recipe(port: int) -> str:
    return (
        f"    search_url_template: http://127.0.0.1:{port}/search.html?q={{query}}\n"
        "    result_item_selector: li.product\n"
        "    title_selector: h2.title\n"
        "    price_selector: span.price\n"
        "    link_selector: a\n"
    )


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


# ------------------------------------------------------------------ funcoes puras
def test_url_to_template_query_string_path_and_accents():
    to = mapper_module._url_to_template
    assert to("https://s.com/busca?busca=evangelho&x=1", "evangelho") == "https://s.com/busca?busca={query}&x=1"
    assert to("https://s.com/Evangelho", "evangelho") == "https://s.com/{query_path}"
    assert to("https://s.com/evangelho?_q=evangelho&map=ft", "evangelho") == (
        "https://s.com/{query_path}?_q={query}&map=ft"
    )
    assert to("https://s.com/busca?id=%7Babc%7D&q=evangelho", "evangelho") == (
        "https://s.com/busca?id=%7Babc%7D&q={query}"
    )
    assert to("https://evangelho.com/home", "evangelho") is None  # o termo so aparece no host
    assert to("https://s.com/busca", "evangelho") is None


def test_build_search_url_encodes_query_and_path_differently():
    template = "https://s.com/{query_path}?q={query}"
    assert build_search_url(template, "o pequeno principe") == "https://s.com/o%20pequeno%20principe?q=o+pequeno+principe"


def test_evaluate_results_requires_titles_and_prices():
    ok = [RawResult("A", 10.0), RawResult("B", 12.0), RawResult("C", None)]
    assert evaluate_results(ok).ok is True
    assert evaluate_results([]).ok is False
    no_price = [RawResult("A", None), RawResult("B", None)]
    report = evaluate_results(no_price)
    assert report.ok is False and "preco" in report.describe()


# ------------------------------------------------------------ modo "type" do scraper
def test_generic_scraper_type_mode(local_server, browser):
    cfg = CompetitorConfig(
        key="t", name="t", base_url=f"http://127.0.0.1:{local_server}", module="m", class_name="c",
        search_mode="type", search_input_selector="input[name='q']",
        result_item_selector="li.product", title_selector="h2.title", price_selector="span.price",
        link_selector="a",
    )
    context = new_stealth_context(browser)
    try:
        results = GenericCssScraper(cfg).search_once(context.new_page(), "harry")
    finally:
        context.close()
    assert [r.title for r in results][0] == "Harry Potter e a Pedra Filosofal"
    assert results[0].price == 35.50
    assert results[0].url == f"http://127.0.0.1:{local_server}/livro/harry-potter-pedra-filosofal"


# --------------------------------------------------------------------- mapeamento
def test_map_competitor_discovers_search_validates_and_saves(project, browser):
    ai = FakeAI(GOOD_ANSWER)
    result = map_competitor("fake", client=ai, browser=browser, progress=lambda _m: None)

    assert result.success and result.state == "mapped" and ai.calls == 1
    saved = config_module.get_competitor("fake")
    assert saved.search_url_template == f"http://127.0.0.1:{project.port}/search.html?q={{query}}"
    assert saved.result_item_selector == "li.product" and saved.price_selector == "span.price"
    assert saved.mapped_by_ai is True and saved.mapped_at and saved.status == "ok"
    assert saved.availability_selector is None
    # o backup da receita anterior (vazia) foi guardado e da para desfazer
    assert len(config_module.list_recipe_backups("fake")) == 1
    config_module.restore_last_recipe_backup("fake")
    assert config_module.get_competitor("fake").has_recipe is False


def test_bad_selectors_are_fed_back_to_the_ai_and_retried(project, browser):
    ai = FakeAI(BAD_ANSWER, GOOD_ANSWER)
    result = map_competitor("fake", client=ai, browser=browser, progress=lambda _m: None)

    assert result.success and ai.calls == 2
    assert "REJEITADA" in ai.prompts[1] and "li.nao-existe" in ai.prompts[1]
    assert "0 itens" in ai.prompts[1]
    assert config_module.get_competitor("fake").result_item_selector == "li.product"


def test_failed_mapping_saves_nothing(project, browser):
    ai = FakeAI(BAD_ANSWER)
    before = (config_module.COMPETITORS_CONFIG_PATH).read_text(encoding="utf-8")
    result = map_competitor("fake", client=ai, browser=browser, progress=lambda _m: None)

    assert not result.success and result.state == "failed"
    assert ai.calls <= mapper_module.MAX_AI_CALLS
    assert config_module.COMPETITORS_CONFIG_PATH.read_text(encoding="utf-8") == before
    assert config_module.list_recipe_backups("fake") == []


def test_ai_saying_page_is_not_results_is_a_clean_failure(project, browser):
    ai = FakeAI({"error": "pagina de captcha"})
    result = map_competitor("fake", client=ai, browser=browser, progress=lambda _m: None)
    assert not result.success and "captcha" in result.message


def test_map_competitor_on_dead_site_marks_down_without_saving_recipe(project, browser):
    project.write(base_url=f"http://127.0.0.1:{closed_port()}")
    result = map_competitor("fake", client=ForbiddenAI(), browser=browser, progress=lambda _m: None)
    assert result.state == "down" and not result.success
    assert config_module.get_competitor("fake").status == "fora_do_ar"


def test_map_competitors_shares_browser_and_isolates_failures(project):
    result = map_competitors(["fake"], client=FakeAI(GOOD_ANSWER), progress=lambda _m: None)
    assert [(key, r.success) for key, r in result] == [("fake", True)]


# ------------------------------------------------------------------ auto-reparo
def test_healthy_recipe_costs_zero_tokens(project, browser):
    project.write(extra=working_recipe(project.port))
    cfg = config_module.get_competitor("fake")
    outcome = ensure_healthy(cfg, browser, client=ForbiddenAI(), progress=lambda _m: None)
    assert outcome.state == "ok" and outcome.usable
    assert config_module.get_competitor("fake").status == "ok"  # corrigiu o status "precisa_mapear"


def test_site_down_is_detected_without_calling_the_ai(project, browser):
    project.write(
        extra=(
            f"    search_url_template: http://127.0.0.1:{closed_port()}/search.html?q={{query}}\n"
            "    result_item_selector: li.product\n    title_selector: h2.title\n    price_selector: span.price\n"
        ),
        base_url=f"http://127.0.0.1:{closed_port()}",
    )
    outcome = ensure_healthy(config_module.get_competitor("fake"), browser, client=ForbiddenAI(), progress=lambda _m: None)
    assert outcome.state == "down" and not outcome.usable
    assert config_module.get_competitor("fake").status == "fora_do_ar"


def test_layout_change_triggers_repair_with_ai(project, browser):
    project.write(
        extra=working_recipe(project.port).replace("li.product", "li.layout-antigo"),
    )
    ai = FakeAI(GOOD_ANSWER)
    outcome = ensure_healthy(config_module.get_competitor("fake"), browser, client=ai, progress=lambda _m: None)

    assert outcome.state == "repaired" and outcome.usable and ai.calls == 1
    assert "parou de funcionar" in ai.prompts[0] or "0 itens" in ai.prompts[0]
    fixed = config_module.get_competitor("fake")
    assert fixed.result_item_selector == "li.product" and fixed.mapped_by_ai and fixed.status == "ok"
    assert len(config_module.list_recipe_backups("fake")) == 1  # backup da receita quebrada


def test_recipe_missing_triggers_mapping(project, browser):
    ai = FakeAI(GOOD_ANSWER)
    outcome = ensure_healthy(config_module.get_competitor("fake"), browser, client=ai, progress=lambda _m: None)
    assert outcome.state == "mapped" and ai.calls == 1


def test_failed_repair_enters_cooldown_and_stops_spending_tokens(project, browser):
    project.write(extra=working_recipe(project.port).replace("li.product", "li.layout-antigo"))
    ai = FakeAI(BAD_ANSWER)
    first = ensure_healthy(config_module.get_competitor("fake"), browser, client=ai, progress=lambda _m: None)
    assert first.state == "failed" and not first.usable
    spent = ai.calls
    assert spent > 0

    second = ensure_healthy(config_module.get_competitor("fake"), browser, client=ai, progress=lambda _m: None)
    assert second.state == "skipped" and not second.usable
    assert ai.calls == spent  # nenhuma chamada nova


def test_ai_unavailable_becomes_failed_outcome(project, browser, monkeypatch):
    def boom(*args, **kwargs):
        raise mapper_module.AIConfigError("sem chave")

    monkeypatch.setattr(mapper_module, "ClaudeClient", boom)
    outcome = ensure_healthy(config_module.get_competitor("fake"), browser, progress=lambda _m: None)
    assert outcome.state == "failed" and "sem chave" in outcome.message


# ------------------------------------------------- integracao com coletor / smoke-test
def test_collector_skips_unusable_competitor_and_records_errors(project, monkeypatch, tmp_path):
    from id_monitor import collector as collector_module
    from id_monitor.storage.db import (
        CollectionStatus, PriceHistory, Product, ProductOrigin, get_session, init_db,
    )

    monkeypatch.setattr("id_monitor.storage.db.DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr("id_monitor.storage.db._engine", None)
    monkeypatch.setattr("id_monitor.storage.db._SessionFactory", None)
    init_db()
    # nao anexar FileHandler de log a este processo de teste
    monkeypatch.setattr(collector_module, "_setup_logging", lambda: None)

    cfg = config_module.get_competitor("fake")
    monkeypatch.setattr("id_monitor.ai.settings.auto_repair_enabled", lambda: True)
    monkeypatch.setattr(
        "id_monitor.ai.mapper.ensure_healthy",
        lambda c, b, **kw: HealthOutcome(c, "down", "Site fora do ar de mentira"),
    )

    session = get_session()
    session.add(Product(title="Livro X", origin=ProductOrigin.MANUAL, active=True))
    session.commit()

    assert collector_module.collect_prices() == 1
    entry = get_session().query(PriceHistory).one()
    assert entry.status == CollectionStatus.ERROR and "fora do ar de mentira" in entry.error_message
    assert cfg.key == "fake"


def test_smoke_test_without_key_never_touches_ai(project, monkeypatch):
    from id_monitor import smoke_test as smoke_module

    project.write(extra=working_recipe(project.port))
    monkeypatch.setattr(smoke_module, "load_competitors", lambda active_only=True: [config_module.get_competitor("fake")])
    monkeypatch.setattr(
        "id_monitor.ai.mapper.ensure_healthy", lambda *a, **k: (_ for _ in ()).throw(AssertionError("IA acionada"))
    )
    results = smoke_module.run_smoke_test(probe_query="harry")
    assert results[0].ok and results[0].note is None


# ------------------------------------------------- site bloqueou o robo (firewall/captcha)
def test_blocked_site_is_reported_without_calling_the_ai(project, browser):
    project.write(extra=working_recipe(project.port).replace("search.html", "blocked.html"))
    outcome = ensure_healthy(config_module.get_competitor("fake"), browser, client=ForbiddenAI(), progress=lambda _m: None)
    assert outcome.state == "blocked" and not outcome.usable
    assert "bloqueou" in outcome.message
    assert config_module.get_competitor("fake").status != "fora_do_ar"  # nao e "fora do ar"


def test_blocked_page_raises_blocked_error_and_is_not_retried(project, browser, monkeypatch):
    from id_monitor.scrapers import base
    from id_monitor.scrapers.base import ScraperBlockedError
    from id_monitor.scrapers.generic_css import GenericCssScraper

    project.write(extra=working_recipe(project.port).replace("search.html", "blocked.html"))
    scraper = GenericCssScraper(config_module.get_competitor("fake"))
    monkeypatch.setattr(base.time, "sleep", lambda _s: (_ for _ in ()).throw(AssertionError("nao deveria esperar/retry")))
    page = browser.new_page()
    with pytest.raises(ScraperBlockedError):
        scraper.search(page, "livro")
    page.close()


def test_detect_block_markers():
    from id_monitor.scrapers.base import detect_block

    assert detect_block("Firewall Analysis", "Access blocked by the system firewall")
    assert detect_block("Just a moment...", "")
    assert detect_block("Livros", "Nenhum produto encontrado para sua busca") is None


def test_collector_stops_hammering_a_blocked_competitor(project, monkeypatch, tmp_path):
    from id_monitor import collector as collector_module
    from id_monitor.scrapers.base import ScraperBlockedError
    from id_monitor.storage.db import (
        CollectionStatus, Competitor, PriceHistory, Product, ProductOrigin, get_session, init_db,
    )

    monkeypatch.setattr("id_monitor.storage.db.DB_PATH", tmp_path / "t2.db")
    monkeypatch.setattr("id_monitor.storage.db._engine", None)
    monkeypatch.setattr("id_monitor.storage.db._SessionFactory", None)
    init_db()
    session = get_session()
    comp = Competitor(key="fake", name="Fake", base_url="http://x", active=True)
    books = [Product(title=f"Livro {i}", origin=ProductOrigin.MANUAL, active=True) for i in range(3)]
    session.add_all([comp, *books])
    session.commit()

    class BlockedScraper:
        calls = 0

        def search(self, page, query):
            BlockedScraper.calls += 1
            raise ScraperBlockedError("O site bloqueou o acesso automatico ('firewall').")

    assert collector_module._collect_single(
        session=session, product=books[0], db_competitor=comp, scraper=BlockedScraper(), page=None
    ) is True
    entry = session.query(PriceHistory).one()
    assert entry.status == CollectionStatus.ERROR and "bloqueou" in entry.error_message
    assert BlockedScraper.calls == 1  # sem tentar pelo titulo depois do ISBN/bloqueio
