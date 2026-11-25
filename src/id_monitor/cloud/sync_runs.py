"""Grava runs e resultados de coleta no Firestore apos o ciclo."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from id_monitor.cloud.firestore_store import (
    CloudStoreError,
    add_run_results,
    create_run,
    finish_run,
)
from id_monitor.cloud.settings import cloud_configured
from id_monitor.reporting.results import latest_results
from id_monitor.storage.db import get_session

logger = logging.getLogger("id_monitor.cloud")


def snapshot_results_for_cloud() -> list[dict[str, Any]]:
    """Serializa o snapshot mais recente (mesma base da aba Resultados)."""
    session = get_session()
    rows = latest_results(session)
    out: list[dict[str, Any]] = []
    for row in rows:
        out.append(
            {
                "id": f"{row.product_id}_{row.competitor_key}",
                "product_id": row.product_id,
                "product_title": row.title,
                "product_isbn": row.isbn,
                "competitor_key": row.competitor_key,
                "competitor_name": row.competitor_name,
                "status": row.status_label,
                "price": row.price,
                "price_regular": row.price_regular,
                "ide_price": row.ide_price,
                "diff": row.diff,
                "verdict": row.verdict,
                "availability": row.availability,
                "product_url": row.url,
                "collected_at": row.collected_at.isoformat() if row.collected_at else None,
                "error_message": row.error,
            }
        )
    return out


def start_cloud_run_safe() -> str:
    """Cria run id; tenta gravar no Firestore se configurado."""
    try:
        return create_run(status="running")
    except CloudStoreError as exc:
        logger.warning("Nao foi possivel criar run no Firestore: %s", exc)
        from datetime import datetime, timezone
        import socket

        return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + socket.gethostname()[:24]


def publish_collection_run(
    *,
    run_id: str,
    records_written: int,
    report_path: Path | str | None = None,
    email_status: str | None = None,
    error_message: str | None = None,
    status: str = "ok",
) -> None:
    if not cloud_configured():
        return
    try:
        results = snapshot_results_for_cloud()
        add_run_results(run_id, results)
        finish_run(
            run_id,
            status=status,
            records_written=records_written,
            report_path=str(report_path) if report_path else None,
            email_status=email_status,
            error_message=error_message,
        )
    except CloudStoreError as exc:
        logger.warning("Falha ao publicar run no Firestore: %s", exc)
