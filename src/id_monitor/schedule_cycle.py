"""Ciclo agendado: collect → report → sync Firestore → e-mail."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from id_monitor.cloud.sync_runs import publish_collection_run, start_cloud_run_safe
from id_monitor.collector import collect_prices
from id_monitor.reporting.report import build_report

logger = logging.getLogger("id_monitor.schedule")
Progress = Callable[[str], None]


@dataclass
class ScheduledCycleResult:
    records_written: int
    report_path: Path | None
    run_id: str
    email_status: str
    error_message: str | None = None


def run_scheduled_cycle(progress: Progress | None = None) -> ScheduledCycleResult:
    """Executa o ciclo completo usado pela tarefa do Windows Task Scheduler."""
    progress = progress or (lambda m: logger.info(m))
    run_id = start_cloud_run_safe()
    progress(f"Ciclo iniciado (run {run_id}).")
    report_path: Path | None = None
    email_status = "skipped"
    error_message = None
    records = 0
    status = "ok"
    try:
        progress("Coletando precos...")
        records = collect_prices()
        progress(f"Coleta concluida: {records} registro(s).")
        progress("Gerando relatorio Excel...")
        report_path = build_report()
        progress(f"Relatorio: {report_path}")

        try:
            from id_monitor.notify.email_resend import EmailError, send_report_email

            progress("Enviando e-mail...")
            result = send_report_email(report_path)
            email_status = f"sent:{result.provider_id or 'ok'}"
            progress(f"E-mail enviado ({email_status}).")
        except Exception as exc:  # noqa: BLE001 - ciclo continua mesmo se e-mail falhar
            email_status = f"failed:{exc}"
            progress(f"E-mail nao enviado: {exc}")
            logger.warning("Falha no e-mail do ciclo: %s", exc)

        publish_collection_run(
            run_id=run_id,
            records_written=records,
            report_path=report_path,
            email_status=email_status,
            status=status,
        )
    except Exception as exc:  # noqa: BLE001
        status = "error"
        error_message = str(exc)
        progress(f"Ciclo falhou: {exc}")
        publish_collection_run(
            run_id=run_id,
            records_written=records,
            report_path=report_path,
            email_status=email_status,
            error_message=error_message,
            status=status,
        )
        raise

    return ScheduledCycleResult(
        records_written=records,
        report_path=report_path,
        run_id=run_id,
        email_status=email_status,
        error_message=error_message,
    )
