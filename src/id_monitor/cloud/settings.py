"""Leitura de config/cloud.yaml."""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from id_monitor.config import CONFIG_DIR, PROJECT_ROOT

CLOUD_CONFIG_PATH = CONFIG_DIR / "cloud.yaml"


@dataclass
class CloudSettings:
    enabled: bool = False
    project_id: str = "ide-digital"
    collection: str = "price_monitor"
    service_account_json: str = ""
    from_email: str = ""
    recipients: list[str] = field(default_factory=list)
    email_provider: str = "resend"

    @property
    def service_account_path(self) -> Path | None:
        raw = (self.service_account_json or "").strip()
        if not raw:
            return None
        path = Path(raw)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path


def load_cloud_settings() -> CloudSettings:
    if not CLOUD_CONFIG_PATH.exists():
        return CloudSettings()
    raw: dict[str, Any] = yaml.safe_load(CLOUD_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    email = raw.get("email") or {}
    recipients = email.get("recipients") or []
    if isinstance(recipients, str):
        recipients = [r.strip() for r in recipients.split(",") if r.strip()]
    return CloudSettings(
        enabled=bool(raw.get("enabled", False)),
        project_id=str(raw.get("project_id") or "ide-digital").strip(),
        collection=str(raw.get("collection") or "price_monitor").strip(),
        service_account_json=str(raw.get("service_account_json") or "").strip(),
        from_email=str(email.get("from_email") or "").strip(),
        recipients=[str(r).strip() for r in recipients if str(r).strip()],
        email_provider=str(email.get("provider") or "resend").strip().lower(),
    )


def save_cloud_settings(settings: CloudSettings) -> None:
    data = {
        "enabled": settings.enabled,
        "project_id": settings.project_id,
        "collection": settings.collection,
        "service_account_json": settings.service_account_json,
        "email": {
            "provider": settings.email_provider,
            "from_email": settings.from_email,
            "recipients": list(settings.recipients),
        },
    }
    CLOUD_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CLOUD_CONFIG_PATH.write_text(
        yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )


def cloud_configured() -> bool:
    settings = load_cloud_settings()
    path = settings.service_account_path
    return bool(settings.enabled and path and path.exists())
