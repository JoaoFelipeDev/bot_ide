"""Ciclo agendado com collect/report/email mockados."""
from __future__ import annotations

from pathlib import Path

from id_monitor import schedule_cycle


def test_run_scheduled_cycle_happy_path(monkeypatch, tmp_path):
    report = tmp_path / "rel.xlsx"
    report.write_bytes(b"xlsx")
    logs: list[str] = []

    monkeypatch.setattr(schedule_cycle, "start_cloud_run_safe", lambda: "run_test")
    monkeypatch.setattr(schedule_cycle, "collect_prices", lambda: 3)
    monkeypatch.setattr(schedule_cycle, "build_report", lambda: report)

    class FakeEmail:
        provider_id = "msg_1"

    def fake_send(path):
        assert Path(path) == report
        return FakeEmail()

    monkeypatch.setattr(
        "id_monitor.notify.email_resend.send_report_email",
        fake_send,
    )
    published = {}

    def fake_publish(**kwargs):
        published.update(kwargs)

    monkeypatch.setattr(schedule_cycle, "publish_collection_run", fake_publish)

    result = schedule_cycle.run_scheduled_cycle(progress=logs.append)
    assert result.records_written == 3
    assert result.run_id == "run_test"
    assert result.email_status.startswith("sent:")
    assert published["status"] == "ok"
    assert any("Coletando" in m for m in logs)


def test_run_scheduled_cycle_email_failure_still_ok(monkeypatch, tmp_path):
    report = tmp_path / "rel.xlsx"
    report.write_bytes(b"x")
    monkeypatch.setattr(schedule_cycle, "start_cloud_run_safe", lambda: "run2")
    monkeypatch.setattr(schedule_cycle, "collect_prices", lambda: 1)
    monkeypatch.setattr(schedule_cycle, "build_report", lambda: report)

    def boom(_path):
        raise RuntimeError("sem credito")

    monkeypatch.setattr("id_monitor.notify.email_resend.send_report_email", boom)
    monkeypatch.setattr(schedule_cycle, "publish_collection_run", lambda **k: None)
    result = schedule_cycle.run_scheduled_cycle()
    assert result.email_status.startswith("failed:")
    assert result.report_path == report
