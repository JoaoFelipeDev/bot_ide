"""Fixtures compartilhadas: isolam os testes da chave de IA/cofre reais da maquina."""
from __future__ import annotations

import keyring
import pytest
from keyring.backend import KeyringBackend


class InMemoryKeyring(KeyringBackend):
    """Cofre de credenciais falso, em memoria, para os testes nao tocarem no cofre real."""

    priority = 1

    def __init__(self) -> None:
        super().__init__()
        self._store: dict[tuple[str, str], str] = {}

    def get_password(self, service, username):
        return self._store.get((service, username))

    def set_password(self, service, username, password):
        self._store[(service, username)] = password

    def delete_password(self, service, username):
        from keyring.errors import PasswordDeleteError

        if (service, username) not in self._store:
            raise PasswordDeleteError("not found")
        del self._store[(service, username)]


@pytest.fixture(autouse=True)
def isolated_ai_environment(monkeypatch):
    """Por padrao nenhum teste ve chave de IA (cofre vazio, sem variavel de ambiente) e o
    auto-reparo fica desligado, entao nenhum teste existente aciona a IA sem querer."""
    backend = InMemoryKeyring()
    previous = keyring.get_keyring()
    keyring.set_keyring(backend)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    yield backend
    keyring.set_keyring(previous)


@pytest.fixture(autouse=True)
def isolated_collector_log(monkeypatch, tmp_path_factory):
    """Os testes nao devem escrever no logs/collector.log real do usuario."""
    monkeypatch.setattr("id_monitor.collector.LOGS_DIR", tmp_path_factory.mktemp("logs"))
