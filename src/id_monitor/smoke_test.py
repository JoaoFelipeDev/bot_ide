"""Smoke-test dos scrapers: roda uma busca de diagnostico em cada concorrente ativo e reporta
se o scraper ainda esta funcionando (retornou resultados) ou parece quebrado (0 resultados ou
erro). Nao grava nada em price_history - e so um check de saude rapido.

Pensado para ser rodado manualmente (via `id-monitor smoke-test`) quando o relatorio mensal
vier com muitos "nao encontrado"/"erro na coleta" suspeitos, para descobrir rapidamente se o
problema e um selector CSS quebrado (o site mudou o layout) em vez de rodar a coleta completa
de novo para descobrir isso.
"""
from __future__ import annotations

import logging

from playwright.sync_api import sync_playwright

from id_monitor.config import load_competitors
from id_monitor.scrapers.base import ScraperSearchError, human_delay, new_stealth_context

logger = logging.getLogger("id_monitor.smoke_test")

# Termo de busca generico, com boa chance de retornar algo em qualquer livraria/editora
# brasileira (o mesmo termo de teste usado pelo mapeamento por IA).
DEFAULT_PROBE_QUERY = "evangelho"


class SmokeTestResult:
    def __init__(self, competitor_key: str, competitor_name: str) -> None:
        self.competitor_key = competitor_key
        self.competitor_name = competitor_name
        self.ok = False
        self.result_count = 0
        self.error: str | None = None
        # Mensagem extra quando a IA mapeou/reparou (ou tentou) o site durante o teste.
        self.note: str | None = None

    @property
    def status_label(self) -> str:
        if self.error:
            label = "FALHA (erro)"
        elif self.result_count == 0:
            label = "ALERTA (0 resultados - selector pode estar quebrado)"
        else:
            label = f"OK ({self.result_count} resultado(s))"
        return f"{label} - {self.note}" if self.note else label


def run_smoke_test(
    probe_query: str = DEFAULT_PROBE_QUERY, auto_repair: bool | None = None
) -> list[SmokeTestResult]:
    """Roda uma busca de diagnostico em cada concorrente ativo e retorna um resultado por
    concorrente, indicando se o scraper parece saudavel.

    auto_repair: se True (ou None e houver chave de IA + reparo automatico ligado), concorrentes
    sem receita ou com a receita quebrada sao mapeados/reparados pela IA antes do teste.
    """
    from id_monitor.ai.settings import auto_repair_enabled

    use_ai = auto_repair_enabled() if auto_repair is None else (auto_repair and auto_repair_enabled())
    competitors = load_competitors(active_only=True)
    results: list[SmokeTestResult] = []

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for competitor_cfg in competitors:
                outcome = SmokeTestResult(competitor_cfg.key, competitor_cfg.name)

                if use_ai:
                    from id_monitor.ai.mapper import ensure_healthy

                    health = ensure_healthy(competitor_cfg, browser, progress=logger.info)
                    competitor_cfg = health.cfg
                    if health.state in ("mapped", "repaired"):
                        outcome.note = "mapeado/reparado pela IA"
                    elif not health.usable:
                        outcome.error = health.message
                        results.append(outcome)
                        continue

                context = new_stealth_context(browser)
                try:
                    scraper_cls = competitor_cfg.load_scraper_class()
                    scraper = scraper_cls(competitor_cfg)
                    page = context.new_page()
                    candidates = scraper.search(page, probe_query)
                    outcome.result_count = len(candidates)
                    outcome.ok = outcome.result_count > 0
                except ScraperSearchError as exc:
                    outcome.error = str(exc)
                except Exception as exc:  # noqa: BLE001 - diagnostico nao deve travar em nenhum caso
                    logger.exception("Erro inesperado no smoke-test de '%s'", competitor_cfg.key)
                    outcome.error = str(exc)
                finally:
                    context.close()
                results.append(outcome)
                human_delay()
        finally:
            browser.close()

    return results
