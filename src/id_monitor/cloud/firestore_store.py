"""Leitura/escrita no Firestore compartilhado (colecao price_monitor).

Layout:
  price_monitor/settings          — agenda, e-mails, flags
  price_monitor/secrets           — blobs Fernet (resend_api_key, ...)
  price_monitor/data/books/{id}   — livros monitorados
  price_monitor/data/runs/{id}    — metadados de cada ciclo
  price_monitor/data/runs/{id}/results/{id} — precos por livro x concorrente
"""
from __future__ import annotations

import logging
import socket
from datetime import datetime, timezone
from typing import Any

from id_monitor.cloud.crypto import CryptoError, decrypt_text, encrypt_text
from id_monitor.cloud.settings import CloudSettings, cloud_configured, load_cloud_settings

logger = logging.getLogger("id_monitor.cloud")

WEEKDAY_KEYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


class CloudStoreError(RuntimeError):
    """Falha ao acessar o Firestore compartilhado."""


def _client(settings: CloudSettings | None = None):
    settings = settings or load_cloud_settings()
    path = settings.service_account_path
    if not settings.enabled or path is None or not path.exists():
        raise CloudStoreError(
            "Nuvem nao configurada. Ative em config/cloud.yaml e aponte o JSON da conta de servico."
        )
    try:
        from google.cloud import firestore
        from google.oauth2 import service_account
    except ImportError as exc:
        raise CloudStoreError(
            "Pacote google-cloud-firestore nao instalado. Rode: pip install google-cloud-firestore"
        ) from exc
    credentials = service_account.Credentials.from_service_account_file(str(path))
    return firestore.Client(project=settings.project_id, credentials=credentials)


def _col(settings: CloudSettings | None = None):
    settings = settings or load_cloud_settings()
    return _client(settings).collection(settings.collection)


def _data_doc(settings: CloudSettings | None = None):
    return _col(settings).document("data")


def test_connection(settings: CloudSettings | None = None) -> str:
    """Grava um ping e le de volta. Devolve mensagem de sucesso ou levanta CloudStoreError."""
    settings = settings or load_cloud_settings()
    col = _col(settings)
    ref = col.document("settings")
    snap = ref.get()
    project = settings.project_id
    if snap.exists:
        return f"Conectado ao projeto {project} (settings ja existe)."
    ref.set({"_ping": True, "updated_at": datetime.now(timezone.utc).isoformat()}, merge=True)
    return f"Conectado ao projeto {project}; documento settings criado."


# ------------------------------------------------------------------ settings
def default_monitor_settings() -> dict[str, Any]:
    return {
        "schedule": {
            "enabled": False,
            "weekdays": ["mon", "wed", "fri"],
            "time": "10:00",
        },
        "email": {
            "from_email": "",
            "recipients": [],
        },
        "updated_at": None,
    }


def get_monitor_settings(settings: CloudSettings | None = None) -> dict[str, Any]:
    settings = settings or load_cloud_settings()
    local = default_monitor_settings()
    local["email"]["from_email"] = settings.from_email
    local["email"]["recipients"] = list(settings.recipients)
    if not cloud_configured():
        return local
    snap = _col(settings).document("settings").get()
    if not snap.exists:
        return local
    data = snap.to_dict() or {}
    schedule = data.get("schedule") or {}
    email = data.get("email") or {}
    return {
        "schedule": {
            "enabled": bool(schedule.get("enabled", False)),
            "weekdays": list(schedule.get("weekdays") or local["schedule"]["weekdays"]),
            "time": str(schedule.get("time") or local["schedule"]["time"]),
        },
        "email": {
            "from_email": str(email.get("from_email") or settings.from_email or ""),
            "recipients": list(email.get("recipients") or settings.recipients or []),
        },
        "updated_at": data.get("updated_at"),
    }


def save_monitor_settings(payload: dict[str, Any], settings: CloudSettings | None = None) -> None:
    settings = settings or load_cloud_settings()
    body = {
        "schedule": payload.get("schedule") or default_monitor_settings()["schedule"],
        "email": payload.get("email") or default_monitor_settings()["email"],
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    weekdays = [d for d in body["schedule"].get("weekdays", []) if d in WEEKDAY_KEYS]
    body["schedule"]["weekdays"] = weekdays
    _col(settings).document("settings").set(body, merge=True)


# ------------------------------------------------------------------- secrets
def save_secret(name: str, plain: str, settings: CloudSettings | None = None) -> None:
    settings = settings or load_cloud_settings()
    try:
        cipher = encrypt_text(plain)
    except CryptoError as exc:
        raise CloudStoreError(str(exc)) from exc
    _col(settings).document("secrets").set(
        {name: cipher, "updated_at": datetime.now(timezone.utc).isoformat()},
        merge=True,
    )


def load_secret(name: str, settings: CloudSettings | None = None) -> str | None:
    settings = settings or load_cloud_settings()
    if not cloud_configured():
        return None
    snap = _col(settings).document("secrets").get()
    if not snap.exists:
        return None
    data = snap.to_dict() or {}
    token = data.get(name)
    if not token:
        return None
    try:
        return decrypt_text(str(token))
    except CryptoError as exc:
        logger.warning("Segredo %s ilegivel: %s", name, exc)
        return None


# --------------------------------------------------------------------- books
def book_doc_id(product: Any) -> str:
    """ID estavel: ISBN se houver; senao product_{id local}."""
    isbn = getattr(product, "isbn", None) or (product.get("isbn") if isinstance(product, dict) else None)
    if isbn:
        return f"isbn_{isbn}"
    pid = getattr(product, "id", None) or (product.get("id") if isinstance(product, dict) else None)
    return f"local_{pid}"


def upsert_book(product: Any, settings: CloudSettings | None = None) -> None:
    if not cloud_configured():
        return
    settings = settings or load_cloud_settings()
    origin = getattr(product, "origin", None)
    origin_val = origin.value if hasattr(origin, "value") else (origin or "manual")
    created = getattr(product, "created_at", None)
    payload = {
        "title": getattr(product, "title", None) or product.get("title"),
        "author": getattr(product, "author", None) if not isinstance(product, dict) else product.get("author"),
        "isbn": getattr(product, "isbn", None) if not isinstance(product, dict) else product.get("isbn"),
        "sku": getattr(product, "sku", None) if not isinstance(product, dict) else product.get("sku"),
        "price_id_livraria": getattr(product, "price_id_livraria", None)
        if not isinstance(product, dict)
        else product.get("price_id_livraria"),
        "origin": origin_val if not isinstance(product, dict) else product.get("origin", "manual"),
        "active": bool(
            getattr(product, "active", True) if not isinstance(product, dict) else product.get("active", True)
        ),
        "local_id": getattr(product, "id", None) if not isinstance(product, dict) else product.get("id"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "created_at": created.isoformat() if hasattr(created, "isoformat") else created,
    }
    _data_doc(settings).collection("books").document(book_doc_id(product)).set(payload, merge=True)


def delete_book_remote(product: Any, settings: CloudSettings | None = None) -> None:
    if not cloud_configured():
        return
    settings = settings or load_cloud_settings()
    _data_doc(settings).collection("books").document(book_doc_id(product)).delete()


def clear_books_remote(settings: CloudSettings | None = None) -> int:
    if not cloud_configured():
        return 0
    settings = settings or load_cloud_settings()
    col = _data_doc(settings).collection("books")
    count = 0
    for doc in col.stream():
        doc.reference.delete()
        count += 1
    return count


def list_books_remote(settings: CloudSettings | None = None) -> list[dict[str, Any]]:
    if not cloud_configured():
        return []
    settings = settings or load_cloud_settings()
    out: list[dict[str, Any]] = []
    for doc in _data_doc(settings).collection("books").stream():
        data = doc.to_dict() or {}
        data["id"] = doc.id
        out.append(data)
    return out


# ---------------------------------------------------------------------- runs
def create_run(
    *,
    status: str = "running",
    records_written: int = 0,
    report_path: str | None = None,
    settings: CloudSettings | None = None,
) -> str:
    settings = settings or load_cloud_settings()
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + socket.gethostname()[:24]
    if not cloud_configured():
        return run_id
    payload = {
        "started_at": datetime.now(timezone.utc).isoformat(),
        "finished_at": None,
        "status": status,
        "hostname": socket.gethostname(),
        "records_written": records_written,
        "report_path": report_path,
        "email_status": None,
        "error_message": None,
    }
    _data_doc(settings).collection("runs").document(run_id).set(payload)
    return run_id


def finish_run(
    run_id: str,
    *,
    status: str,
    records_written: int = 0,
    report_path: str | None = None,
    email_status: str | None = None,
    error_message: str | None = None,
    settings: CloudSettings | None = None,
) -> None:
    if not cloud_configured():
        return
    settings = settings or load_cloud_settings()
    _data_doc(settings).collection("runs").document(run_id).set(
        {
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "status": status,
            "records_written": records_written,
            "report_path": report_path,
            "email_status": email_status,
            "error_message": error_message,
        },
        merge=True,
    )


def add_run_results(
    run_id: str,
    results: list[dict[str, Any]],
    settings: CloudSettings | None = None,
) -> None:
    if not cloud_configured() or not results:
        return
    settings = settings or load_cloud_settings()
    batch_size = 400
    col = _data_doc(settings).collection("runs").document(run_id).collection("results")
    client = _client(settings)
    for i in range(0, len(results), batch_size):
        chunk = results[i : i + batch_size]
        batch = client.batch()
        for idx, row in enumerate(chunk):
            doc_id = str(row.get("id") or f"{i + idx}")
            batch.set(col.document(doc_id), row, merge=True)
        batch.commit()
