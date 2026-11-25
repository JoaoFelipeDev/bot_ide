"""Criptografia de segredos (Fernet) com chave mestra no keyring do Windows."""
from __future__ import annotations

import base64
import logging

import keyring
from cryptography.fernet import Fernet, InvalidToken
from keyring.errors import KeyringError

logger = logging.getLogger("id_monitor.cloud")

SERVICE_NAME = "id-monitor"
MASTER_KEY_USERNAME = "cloud_fernet_master"


class CryptoError(RuntimeError):
    """Falha ao cifrar/decifrar segredos."""


def _get_or_create_master_key() -> bytes:
    try:
        stored = keyring.get_password(SERVICE_NAME, MASTER_KEY_USERNAME)
    except KeyringError as exc:
        raise CryptoError(f"Cofre de credenciais indisponivel: {exc}") from exc
    if stored:
        return stored.encode("ascii")
    key = Fernet.generate_key()
    try:
        keyring.set_password(SERVICE_NAME, MASTER_KEY_USERNAME, key.decode("ascii"))
    except KeyringError as exc:
        raise CryptoError(f"Nao foi possivel guardar a chave mestra: {exc}") from exc
    return key


def encrypt_text(plain: str) -> str:
    """Devolve ciphertext em texto (base64 url-safe) para gravar no Firestore."""
    if plain is None:
        raise CryptoError("Texto vazio nao pode ser cifrado.")
    fernet = Fernet(_get_or_create_master_key())
    token = fernet.encrypt(plain.encode("utf-8"))
    return base64.urlsafe_b64encode(token).decode("ascii")


def decrypt_text(token_b64: str) -> str:
    if not token_b64:
        raise CryptoError("Ciphertext vazio.")
    try:
        raw = base64.urlsafe_b64decode(token_b64.encode("ascii"))
        fernet = Fernet(_get_or_create_master_key())
        return fernet.decrypt(raw).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise CryptoError("Nao foi possivel decifrar o segredo (chave mestra diferente?).") from exc
