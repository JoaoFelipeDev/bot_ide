"""Configuracoes (nao secretas) da IA: modelo e se o reparo automatico esta ligado.

Ficam em data/ai_settings.json (a pasta data/ nao entra no .zip de distribuicao). A chave da
API NAO fica aqui - ver id_monitor.ai.secrets.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from id_monitor import config
from id_monitor.ai.secrets import has_api_key

DEFAULT_MODEL = "claude-haiku-4-5"
SUGGESTED_MODELS = ("claude-haiku-4-5", "claude-sonnet-4-5")


@dataclass
class AISettings:
    model: str = DEFAULT_MODEL
    auto_repair: bool = True


def _settings_path():
    return config.DATA_DIR / "ai_settings.json"


def load_settings() -> AISettings:
    path = _settings_path()
    if not path.exists():
        return AISettings()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return AISettings()
    return AISettings(
        model=str(raw.get("model") or DEFAULT_MODEL).strip() or DEFAULT_MODEL,
        auto_repair=bool(raw.get("auto_repair", True)),
    )


def save_settings(settings: AISettings) -> None:
    config.ensure_directories()
    _settings_path().write_text(json.dumps(asdict(settings), indent=2), encoding="utf-8")


def auto_repair_enabled() -> bool:
    """True se ha chave configurada E o reparo automatico esta ligado."""
    return has_api_key() and load_settings().auto_repair
