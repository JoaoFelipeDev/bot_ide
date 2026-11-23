"""Wrapper fino da API do Claude: manda um prompt e devolve um JSON ja interpretado.

Existe para (1) isolar o resto do codigo do SDK `anthropic`, (2) traduzir os erros da API em
mensagens claras em portugues e (3) permitir trocar o cliente por um falso (mock) nos testes:
o mapeador so depende do metodo `complete_json(system, user)`.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Protocol

import anthropic

from id_monitor.ai.secrets import AIConfigError, get_api_key
from id_monitor.ai.settings import load_settings

logger = logging.getLogger("id_monitor.ai")


class AIClientError(Exception):
    """Falha ao falar com a API do Claude (chave invalida, sem credito, resposta inutilizavel...)."""


class JsonCompleter(Protocol):
    """Contrato minimo que o mapeador espera de um cliente de IA."""

    def complete_json(self, system: str, user: str, max_tokens: int = 1024) -> dict[str, Any]: ...


def parse_json_object(text: str) -> dict[str, Any]:
    """Extrai o primeiro objeto JSON de um texto (tolera cercas ```json e texto em volta)."""
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(.*?)```", cleaned, re.DOTALL)
    if fenced:
        cleaned = fenced.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start == -1 or end <= start:
        raise AIClientError("A resposta da IA nao trouxe um JSON valido.")
    try:
        parsed = json.loads(cleaned[start : end + 1])
    except ValueError as exc:
        raise AIClientError(f"A resposta da IA trouxe um JSON invalido: {exc}") from exc
    if not isinstance(parsed, dict):
        raise AIClientError("A resposta da IA nao e um objeto JSON.")
    return parsed


class ClaudeClient:
    def __init__(self, api_key: str | None = None, model: str | None = None) -> None:
        key = api_key or get_api_key()
        if not key:
            raise AIConfigError(
                "Nenhuma chave da API do Claude configurada. Cole a chave na aba "
                "'Configuracoes de IA' (ou defina a variavel de ambiente ANTHROPIC_API_KEY)."
            )
        self.model = model or load_settings().model
        self._client = anthropic.Anthropic(api_key=key, timeout=90.0, max_retries=2)

    def _call(self, system: str, user: str, max_tokens: int) -> str:
        try:
            response = self._client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
        except anthropic.AuthenticationError as exc:
            raise AIClientError("Chave da API invalida ou revogada. Confira a chave salva.") from exc
        except anthropic.PermissionDeniedError as exc:
            raise AIClientError("A chave nao tem permissao para usar este modelo.") from exc
        except anthropic.NotFoundError as exc:
            raise AIClientError(f"Modelo '{self.model}' nao encontrado. Escolha outro modelo.") from exc
        except anthropic.RateLimitError as exc:
            raise AIClientError("Limite de uso da API atingido. Tente novamente em alguns minutos.") from exc
        except anthropic.BadRequestError as exc:
            message = str(exc)
            if "credit" in message.lower():
                raise AIClientError("Sem credito na conta da Anthropic. Adicione saldo e tente de novo.") from exc
            raise AIClientError(f"Requisicao recusada pela API: {message}") from exc
        except anthropic.APIConnectionError as exc:
            raise AIClientError("Sem conexao com a API do Claude. Verifique a internet.") from exc
        except anthropic.APIError as exc:
            raise AIClientError(f"Erro da API do Claude: {exc}") from exc

        parts = [block.text for block in response.content if getattr(block, "type", "") == "text"]
        return "".join(parts)

    def complete_json(self, system: str, user: str, max_tokens: int = 1024) -> dict[str, Any]:
        logger.info("Consultando o Claude (%s)...", self.model)
        return parse_json_object(self._call(system, user, max_tokens))

    def test_connection(self) -> str:
        """Faz uma chamada minima para validar chave + modelo. Retorna o modelo usado."""
        self._call("Responda apenas com a palavra ok.", "ping", max_tokens=16)
        return self.model
