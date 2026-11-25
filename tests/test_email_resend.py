"""Testes do payload Resend (sem rede)."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from id_monitor.notify import email_resend
from id_monitor.reporting.results import ResultRow


def _row(**kw) -> ResultRow:
    base = dict(
        product_id=1, title="Livro", isbn="9780000000001", competitor_key="a",
        competitor_name="A", ide_price=20.0, status_label="ok", price=18.0,
        price_regular=None, availability="sim", url="https://x", confidence=1.0,
        error="", collected_at=datetime(2026, 1, 1, 12, 0, 0),
    )
    base.update(kw)
    return ResultRow(**base)


def test_build_summary_html_counts():
    rows = [
        _row(ide_price=30.0, price=20.0),  # ide_cara
        _row(product_id=2, ide_price=10.0, price=15.0),  # ide_barata
    ]
    html = email_resend.build_summary_html(rows)
    assert "Monitor de Precos" in html
    assert "Seu preco" in html


def test_send_report_email_builds_request(tmp_path, monkeypatch):
    report = tmp_path / "relatorio.xlsx"
    report.write_bytes(b"fake-xlsx")

    monkeypatch.setattr(email_resend, "get_resend_api_key", lambda: "re_test")
    monkeypatch.setattr(email_resend, "cloud_configured", lambda: False)
    monkeypatch.setattr(email_resend, "load_cloud_settings", lambda: MagicMock(
        from_email="monitor@ide.com.br", recipients=["a@x.com"],
    ))
    monkeypatch.setattr(email_resend, "latest_results", lambda: [_row()])

    captured = {}

    class FakeResp:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"id":"msg_123"}'

    def fake_urlopen(req, timeout=0, context=None):
        captured["url"] = req.full_url
        captured["body"] = req.data
        captured["auth"] = req.headers.get("Authorization")
        return FakeResp()

    monkeypatch.setattr(email_resend.urllib.request, "urlopen", fake_urlopen)
    result = email_resend.send_report_email(
        report, recipients=["a@x.com"], from_email="monitor@ide.com.br", api_key="re_test"
    )
    assert result.ok and result.provider_id == "msg_123"
    assert captured["url"] == email_resend.RESEND_URL
    assert b"attachments" in captured["body"]


def test_send_requires_recipients(tmp_path, monkeypatch):
    report = tmp_path / "r.xlsx"
    report.write_bytes(b"x")
    monkeypatch.setattr(email_resend, "get_resend_api_key", lambda: "re_test")
    monkeypatch.setattr(email_resend, "cloud_configured", lambda: False)
    monkeypatch.setattr(email_resend, "load_cloud_settings", lambda: MagicMock(
        from_email="a@b.com", recipients=[],
    ))
    with pytest.raises(email_resend.EmailError, match="destinatario"):
        email_resend.send_report_email(report, recipients=[], from_email="a@b.com", api_key="re_test")
