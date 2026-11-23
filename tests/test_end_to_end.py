"""Teste de ponta a ponta do MVP.

Em vez de depender dos sites reais dos concorrentes (que podem bloquear scraping ou ficar
indisponiveis, como observado durante o desenvolvimento), simulamos um concorrente local via um
pequeno servidor HTTP estatico servindo tests/fixtures/fake_competitor/search.html. Isso valida
o fluxo completo collect -> matching -> price_history -> relatorio sem flakiness de rede.
"""
from __future__ import annotations

import http.server
import threading
from functools import partial
from pathlib import Path

import pytest

from id_monitor import collector as collector_module
from id_monitor.config import CompetitorConfig
from id_monitor.reporting import report as report_module
from id_monitor.storage.db import (
    CollectionStatus,
    PriceHistory,
    Product,
    ProductOrigin,
    get_session,
    init_db,
    sync_competitors_from_config,
)

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "fake_competitor"


@pytest.fixture(scope="module")
def local_server():
    handler_cls = partial(http.server.SimpleHTTPRequestHandler, directory=str(FIXTURE_DIR))
    server = http.server.HTTPServer(("127.0.0.1", 0), handler_cls)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield port
    server.shutdown()
    thread.join(timeout=2)


@pytest.fixture
def fake_competitor_cfg(local_server):
    return CompetitorConfig(
        key="fake_competitor",
        name="Concorrente Fake",
        base_url=f"http://127.0.0.1:{local_server}",
        module="id_monitor.scrapers.generic_css",
        class_name="GenericCssScraper",
        active=True,
        search_url_template=f"http://127.0.0.1:{local_server}/search.html?q={{query}}",
        result_item_selector="li.product",
        title_selector="h2.title",
        price_selector="span.price",
        link_selector="a",
    )


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    """Isola o teste em um SQLite temporario, em vez de usar data/monitor.db real."""
    test_db_path = tmp_path / "test_monitor.db"
    # DB_PATH e importado no topo de id_monitor.storage.db (from ... import DB_PATH), entao o
    # nome vive no namespace do proprio modulo db e precisa ser substituido ali, nao em config.
    monkeypatch.setattr("id_monitor.storage.db.DB_PATH", test_db_path)
    monkeypatch.setattr("id_monitor.storage.db._engine", None)
    monkeypatch.setattr("id_monitor.storage.db._SessionFactory", None)
    init_db()
    return test_db_path


def test_end_to_end_collect_matches_and_report(fake_competitor_cfg, isolated_db, monkeypatch, tmp_path):
    # `sync_competitors_from_config` faz `from id_monitor.config import load_competitors`
    # dentro da propria funcao, entao o patch precisa ser na fonte (id_monitor.config).
    monkeypatch.setattr(
        "id_monitor.config.load_competitors", lambda active_only=True: [fake_competitor_cfg]
    )
    # collector.py e report.py importam load_competitors no topo do modulo, entao o nome
    # vive no namespace de cada um e precisa ser substituido la tambem.
    monkeypatch.setattr(collector_module, "load_competitors", lambda active_only=True: [fake_competitor_cfg])
    monkeypatch.setattr(report_module, "load_competitors", lambda active_only=True: [fake_competitor_cfg])

    session = get_session()
    sync_competitors_from_config(session)

    session.add_all(
        [
            Product(
                title="Harry Potter e a Pedra Filosofal",
                price_id_livraria=39.90,
                origin=ProductOrigin.MANUAL,
                active=True,
            ),
            Product(
                title="O Pequeno Principe",
                price_id_livraria=24.90,
                origin=ProductOrigin.MANUAL,
                active=True,
            ),
            Product(
                title="Livro Totalmente Inexistente Nesse Concorrente",
                price_id_livraria=10.0,
                origin=ProductOrigin.MANUAL,
                active=True,
            ),
        ]
    )
    session.commit()

    records_written = collector_module.collect_prices()
    assert records_written == 3

    session = get_session()
    entries = session.query(PriceHistory).join(Product).all()
    status_by_title = {entry.product.title: entry.status for entry in entries}
    price_by_title = {entry.product.title: entry.price for entry in entries}

    assert status_by_title["Harry Potter e a Pedra Filosofal"] == CollectionStatus.FOUND
    assert price_by_title["Harry Potter e a Pedra Filosofal"] == 35.50

    assert status_by_title["O Pequeno Principe"] == CollectionStatus.FOUND
    assert price_by_title["O Pequeno Principe"] == 22.00

    assert status_by_title["Livro Totalmente Inexistente Nesse Concorrente"] == CollectionStatus.NOT_FOUND

    report_path = report_module.build_report(tmp_path / "relatorio_teste.xlsx")
    assert report_path.exists()
