"""Testes de criptografia Fernet (chave mestra no keyring, mockavel)."""
from __future__ import annotations

import pytest

from id_monitor.cloud import crypto


def test_encrypt_decrypt_roundtrip(monkeypatch):
    fake_store: dict[tuple[str, str], str] = {}

    def get_password(service, user):
        return fake_store.get((service, user))

    def set_password(service, user, value):
        fake_store[(service, user)] = value

    monkeypatch.setattr(crypto.keyring, "get_password", get_password)
    monkeypatch.setattr(crypto.keyring, "set_password", set_password)

    token = crypto.encrypt_text("re_test_secret")
    assert token != "re_test_secret"
    assert crypto.decrypt_text(token) == "re_test_secret"


def test_decrypt_invalid_raises(monkeypatch):
    fake_store: dict[tuple[str, str], str] = {}

    def get_password(service, user):
        return fake_store.get((service, user))

    def set_password(service, user, value):
        fake_store[(service, user)] = value

    monkeypatch.setattr(crypto.keyring, "get_password", get_password)
    monkeypatch.setattr(crypto.keyring, "set_password", set_password)
    crypto.encrypt_text("x")  # cria chave
    with pytest.raises(crypto.CryptoError):
        crypto.decrypt_text("nao-e-token")
