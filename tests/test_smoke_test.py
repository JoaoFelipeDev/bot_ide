"""Testes do smoke-test de scrapers, usando o mesmo concorrente fake local do teste e2e."""
from __future__ import annotations

import http.server
import threading
from functools import partial
from pathlib import Path

import pytest

from id_monitor import smoke_test as smoke_test_module
from id_monitor.config import CompetitorConfig

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


def test_smoke_test_reports_ok_when_selectors_work(local_server, monkeypatch):
    healthy_cfg = CompetitorConfig(
        key="fake_ok",
        name="Fake OK",
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
    monkeypatch.setattr(smoke_test_module, "load_competitors", lambda active_only=True: [healthy_cfg])

    results = smoke_test_module.run_smoke_test(probe_query="harry")

    assert len(results) == 1
    assert results[0].ok is True
    assert results[0].result_count == 3
    assert "OK" in results[0].status_label


def test_smoke_test_reports_alert_when_selector_is_broken(local_server, monkeypatch):
    broken_cfg = CompetitorConfig(
        key="fake_broken",
        name="Fake Quebrado",
        base_url=f"http://127.0.0.1:{local_server}",
        module="id_monitor.scrapers.generic_css",
        class_name="GenericCssScraper",
        active=True,
        search_url_template=f"http://127.0.0.1:{local_server}/search.html?q={{query}}",
        result_item_selector="li.este-seletor-nao-existe",
        title_selector="h2.title",
        price_selector="span.price",
        link_selector="a",
    )
    monkeypatch.setattr(smoke_test_module, "load_competitors", lambda active_only=True: [broken_cfg])

    results = smoke_test_module.run_smoke_test(probe_query="harry")

    assert len(results) == 1
    assert results[0].ok is False
    assert results[0].result_count == 0
    assert "ALERTA" in results[0].status_label
