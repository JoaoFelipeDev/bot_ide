"""Armazenamento da chave da API do Claude.

A chave e guardada no Cofre de Credenciais do Windows (via `keyring`), nunca em arquivo do
projeto - assim ela nao vai parar no .zip de distribuicao nem em backups da pasta. A variavel de
ambiente ANTHROPIC_API_KEY funciona como alternativa (usada se nao houver chave no cofre).
"""
from __future__ import annotations

import logging
import os

import keyring
from keyring.errors import KeyringError

logger = logging.getLogger("id_monitor.ai")

SERVICE_NAME = "id-monitor"
KEY_USERNAME = "anthropic_api_key"
ENV_VAR = "ANTHROPIC_API_KEY"


class AIConfigError(Exception):
    """Problema de configuracao da IA (ex.: sem chave, cofre indisponivel)."""


def get_api_key() -> str | None:
    """Retorna a chave salva no cofre; se nao houver, a da variavel de ambiente; senao None."""
    try:
        stored = keyring.get_password(SERVICE_NAME, KEY_USERNAME)
    except KeyringError as exc:
        logger.warning("Nao foi possivel ler o cofre de credenciais: %s", exc)
        stored = None
    if stored and stored.strip():
        return stored.strip()
    env_value = os.environ.get(ENV_VAR, "").strip()
    return env_value or None


def set_api_key(api_key: str) -> None:
    api_key = api_key.strip()
    if not api_key:
        raise AIConfigError("A chave da API nao pode ficar em branco.")
    try:
        keyring.set_password(SERVICE_NAME, KEY_USERNAME, api_key)
    except KeyringError as exc:
        raise AIConfigError(f"Nao foi possivel salvar a chave no cofre de credenciais: {exc}") from exc


def delete_api_key() -> None:
    try:
        keyring.delete_password(SERVICE_NAME, KEY_USERNAME)
    except keyring.errors.PasswordDeleteError:
        pass  # nao havia chave salva
    except KeyringError as exc:
        raise AIConfigError(f"Nao foi possivel remover a chave do cofre: {exc}") from exc


def has_api_key() -> bool:
    return get_api_key() is not None


def mask_key(api_key: str | None) -> str:
    """Versao segura para exibir na interface/log (ex.: 'sk-ant-...a1b2')."""
    if not api_key:
        return ""
    if len(api_key) <= 10:
        return "*" * len(api_key)
    return f"{api_key[:7]}...{api_key[-4:]}"
