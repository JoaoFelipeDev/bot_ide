"""Envio de relatorio por e-mail via Resend (https://resend.com)."""
from __future__ import annotations

import base64
import json
import logging
import ssl
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

import certifi

from id_monitor.cloud.firestore_store import load_secret, save_secret
from id_monitor.cloud.settings import cloud_configured, load_cloud_settings
from id_monitor.reporting.results import ResultRow, latest_results

logger = logging.getLogger("id_monitor.notify")

RESEND_URL = "https://api.resend.com/emails"
KEYRING_FALLBACK_NAME = "resend_api_key"


class EmailError(RuntimeError):
    """Falha ao enviar e-mail."""


@dataclass
class EmailSendResult:
    ok: bool
    message: str
    provider_id: str | None = None


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _stats(rows: Sequence[ResultRow]) -> dict[str, int]:
    counts: dict[str, int] = {"total": len(rows)}
    for row in rows:
        counts[row.status_label] = counts.get(row.status_label, 0) + 1
        if row.verdict:
            counts[row.verdict] = counts.get(row.verdict, 0) + 1
    return counts


def get_resend_api_key() -> str | None:
    if cloud_configured():
        key = load_secret("resend_api_key")
        if key:
            return key
    try:
        import keyring

        stored = keyring.get_password("id-monitor", KEYRING_FALLBACK_NAME)
        return stored.strip() if stored else None
    except Exception:  # noqa: BLE001
        return None


def set_resend_api_key(api_key: str, *, sync_cloud: bool = True) -> None:
    api_key = api_key.strip()
    if not api_key:
        raise EmailError("API key do Resend vazia.")
    import keyring

    keyring.set_password("id-monitor", KEYRING_FALLBACK_NAME, api_key)
    if sync_cloud and cloud_configured():
        save_secret("resend_api_key", api_key)


def build_summary_html(rows: Sequence[ResultRow] | None = None) -> str:
    rows = list(rows if rows is not None else latest_results())
    stats = _stats(rows)
    lines = [
        "<h2>Monitor de Precos — IDE Livraria</h2>",
        f"<p>Total de linhas: <b>{stats.get('total', len(rows))}</b></p>",
        "<ul>",
    ]
    for key, label in (
        ("ide_cara", "Seu preco mais caro"),
        ("ide_barata", "Seu preco mais barato"),
        ("igual", "Mesmo preco"),
        ("ok", "Encontrados"),
        ("nao encontrado", "Nao encontrados"),
        ("erro na coleta", "Erros"),
    ):
        if key in stats:
            lines.append(f"<li>{label}: {stats[key]}</li>")
    lines.append("</ul>")
    lines.append("<p>O relatorio Excel completo segue em anexo.</p>")
    return "\n".join(lines)


def build_summary_text(rows: Sequence[ResultRow] | None = None) -> str:
    rows = list(rows if rows is not None else latest_results())
    stats = _stats(rows)
    parts = [f"Monitor de Precos IDE — {stats.get('total', len(rows))} linhas."]
    for key in ("ide_cara", "ide_barata", "igual"):
        if key in stats:
            parts.append(f"{key}={stats[key]}")
    return " ".join(parts)


def send_report_email(
    report_path: Path | str,
    *,
    recipients: Sequence[str] | None = None,
    from_email: str | None = None,
    subject: str | None = None,
    api_key: str | None = None,
) -> EmailSendResult:
    settings = load_cloud_settings()
    monitor = {}
    if cloud_configured():
        from id_monitor.cloud.firestore_store import get_monitor_settings

        monitor = get_monitor_settings(settings)

    to_list = list(recipients or (monitor.get("email") or {}).get("recipients") or settings.recipients)
    from_addr = (from_email or (monitor.get("email") or {}).get("from_email") or settings.from_email or "").strip()
    key = (api_key or get_resend_api_key() or "").strip()
    path = Path(report_path)

    if not key:
        raise EmailError("API key do Resend nao configurada.")
    if not from_addr:
        raise EmailError("Remetente (from_email) nao configurado.")
    if not to_list:
        raise EmailError("Nenhum destinatario configurado.")
    if not path.exists():
        raise EmailError(f"Arquivo de relatorio nao encontrado: {path}")

    rows = latest_results()
    subject = subject or f"Monitor de precos IDE — {path.stem}"
    attachment_b64 = base64.b64encode(path.read_bytes()).decode("ascii")
    payload = {
        "from": from_addr,
        "to": to_list,
        "subject": subject,
        "html": build_summary_html(rows),
        "text": build_summary_text(rows),
        "attachments": [
            {
                "filename": path.name,
                "content": attachment_b64,
            }
        ],
    }
    request = urllib.request.Request(
        RESEND_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "User-Agent": "id-monitor-resend/0.3",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60, context=_ssl_context()) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise EmailError(f"Resend HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise EmailError(f"Falha de rede ao falar com Resend: {exc}") from exc

    provider_id = str(body.get("id") or "") or None
    return EmailSendResult(ok=True, message="E-mail enviado.", provider_id=provider_id)
