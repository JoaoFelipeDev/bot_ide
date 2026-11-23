"""Interface comum dos scrapers de concorrentes + utilitarios de navegador anti-bot.

Cada concorrente e representado por uma subclasse de `CompetitorScraper`. A maioria dos sites
pode usar diretamente `GenericCssScraper` (ver generic_css.py), configurada via
config/competitors.yaml. Sites com comportamento especial podem sobrescrever `search()`.
"""
from __future__ import annotations

import logging
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import wraps

from playwright.sync_api import Browser, BrowserContext, Page

logger = logging.getLogger("id_monitor.scrapers")

from id_monitor.config import CompetitorConfig

# Lista pequena de user-agents de navegadores desktop reais e atuais, para parecer trafego
# organico em vez de um unico UA de bot sempre identico.
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/129.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36",
]

VIEWPORTS = [
    {"width": 1366, "height": 768},
    {"width": 1920, "height": 1080},
    {"width": 1440, "height": 900},
]

_PRICE_RE = re.compile(r"(\d{1,3}(?:[.\s]\d{3})*,\d{2}|\d+\.\d{2}|\d+,\d{2}|\d+)")


class ScraperSearchError(Exception):
    """Levantada quando um scraper falha de forma irrecuperavel para uma busca."""


class ScraperNotConfiguredError(ScraperSearchError):
    """O concorrente nao tem receita de busca configurada. Nao adianta tentar de novo (retry)."""


class ScraperBlockedError(ScraperSearchError):
    """O site bloqueou o acesso automatico (firewall, captcha, Cloudflare...). Insistir so piora
    o bloqueio, entao nao ha retry; e nao e problema de receita, entao a IA nao deve ser chamada."""


_BLOCK_MARKERS = (
    "access blocked", "blocked by the system firewall", "firewall analysis", "access denied",
    "acesso negado", "attention required", "just a moment", "checking your browser",
    "unusual traffic", "captcha", "are you a robot", "you have been blocked",
    "verifique que voce e humano", "verifique se voce e humano", "acesso bloqueado",
)


def detect_block(page_title: str, body_text: str) -> str | None:
    """Reconhece paginas de bloqueio pelo titulo/texto. Devolve o trecho que denunciou, ou None."""
    haystack = f"{page_title}\n{body_text[:3000]}".lower()
    for marker in _BLOCK_MARKERS:
        if marker in haystack:
            return marker
    return None


@dataclass
class RawResult:
    """Resultado bruto de uma busca no site do concorrente, antes do matching."""

    title: str
    price: float | None
    price_promo: float | None = None
    availability: str | None = None
    url: str | None = None
    isbn: str | None = None  # quando o card/resultado expoe o ISBN


def human_delay(min_seconds: float = 1.5, max_seconds: float = 4.0) -> None:
    """Pausa um intervalo aleatorio, para nao disparar requisicoes em cadencia robotica."""
    time.sleep(random.uniform(min_seconds, max_seconds))


def human_scroll(page: Page, steps: int = 2) -> None:
    """Rola a pagina em alguns passos pequenos com pausas, em vez de ler o DOM instantaneamente
    apos o carregamento. Reduz um sinal simples de automacao (bots tipicamente nao rolam)."""
    for _ in range(steps):
        try:
            page.mouse.wheel(0, random.randint(300, 900))
        except Exception:  # noqa: BLE001 - rolagem e so um "nice to have", nunca deve quebrar a busca
            return
        time.sleep(random.uniform(0.3, 0.9))


def retry_on_failure(max_attempts: int = 3, base_delay: float = 2.0):
    """Decorator que tenta a funcao novamente (com backoff crescente + jitter) quando ela
    levanta `ScraperSearchError`, antes de desistir e deixar o erro propagar.

    Util para bloqueios/timeouts transitorios (ex.: rate limiting momentaneo), sem precisar
    espalhar logica de retry em cada scraper individualmente.
    """

    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exc: Exception | None = None
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except (ScraperNotConfiguredError, ScraperBlockedError):
                    raise
                except ScraperSearchError as exc:
                    last_exc = exc
                    if attempt == max_attempts:
                        break
                    delay = base_delay * attempt + random.uniform(0, 1.5)
                    logger.info(
                        "Tentativa %d/%d falhou (%s), tentando novamente em %.1fs...",
                        attempt, max_attempts, exc, delay,
                    )
                    time.sleep(delay)
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator


def new_stealth_context(browser: Browser) -> BrowserContext:
    """Cria um BrowserContext com configuracoes que reduzem sinais obvios de automacao
    (user-agent realista, viewport de desktop comum, locale/timezone pt-BR)."""
    context = browser.new_context(
        user_agent=random.choice(USER_AGENTS),
        viewport=random.choice(VIEWPORTS),
        locale="pt-BR",
        timezone_id="America/Sao_Paulo",
    )
    # Remove o sinal mais trivial de automacao (navigator.webdriver) antes de qualquer
    # script da pagina rodar. Isso nao contorna protecoes avancadas (ex.: Cloudflare), apenas
    # evita o bloqueio mais simples baseado nessa flag.
    context.add_init_script(
        "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
    )
    return context


def parse_price(text: str | None) -> float | None:
    """Converte um texto de preco em reais (ex.: 'R$ 39,90', '39.90', '1.234,50') em float."""
    if not text:
        return None
    match = _PRICE_RE.search(text.replace("\xa0", " "))
    if not match:
        return None
    raw = match.group(1)
    if "," in raw:
        raw = raw.replace(".", "").replace(" ", "").replace(",", ".")
    else:
        raw = raw.replace(" ", "")
    try:
        return float(raw)
    except ValueError:
        return None


class CompetitorScraper(ABC):
    """Interface comum que todo scraper de concorrente deve implementar."""

    def __init__(self, competitor_cfg: CompetitorConfig) -> None:
        self.competitor_cfg = competitor_cfg

    @abstractmethod
    def search(self, page: Page, query: str) -> list[RawResult]:
        """Busca `query` (titulo ou ISBN) no site do concorrente e retorna os resultados brutos
        encontrados (pode ser lista vazia se nada foi encontrado). Deve levantar
        `ScraperSearchError` em caso de falha irrecuperavel (ex.: bloqueio, timeout)."""
        raise NotImplementedError
