"""Orquestra a coleta: para cada produto monitorado x concorrente ativo, abre o navegador,
busca o produto, aplica o matching e grava o resultado em price_history.

Falhas em um concorrente/produto sao isoladas (logadas) e nao interrompem os demais.
"""
from __future__ import annotations

import logging
from datetime import datetime

from playwright.sync_api import sync_playwright
from sqlalchemy.orm import Session

from id_monitor.config import LOGS_DIR, ensure_directories, load_competitors
from id_monitor.matching.matcher import match_product
from id_monitor.scrapers.base import (
    ScraperBlockedError,
    ScraperSearchError,
    human_delay,
    new_stealth_context,
)
from id_monitor.storage.db import (
    Competitor,
    CollectionStatus,
    PriceHistory,
    Product,
    get_session,
    sync_competitors_from_config,
)

logger = logging.getLogger("id_monitor.collector")


def _setup_logging() -> None:
    """Garante que o logger 'id_monitor' tenha um FileHandler (logs/collector.log) e um
    StreamHandler (console), sem duplicar se ja existirem - e sem remover outros handlers que
    um chamador externo (ex.: a interface grafica) possa ter adicionado para capturar o
    progresso em tempo real."""
    ensure_directories()
    log_file = LOGS_DIR / "collector.log"
    root = logging.getLogger("id_monitor")
    root.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")

    has_file_handler = any(isinstance(h, logging.FileHandler) for h in root.handlers)
    if not has_file_handler:
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

    has_console_handler = any(
        isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)
        for h in root.handlers
    )
    if not has_console_handler:
        root.addHandler(logging.StreamHandler())


def collect_prices(
    product_titles: list[str] | None = None,
    competitor_keys: list[str] | None = None,
) -> int:
    """Roda a coleta de precos.

    - product_titles: se informado, restringe a coleta a produtos ativos com esse(s) titulo(s)
      (busca exata, sem diferenciar maiusculas/minusculas). Se None, coleta todos os produtos ativos.
    - competitor_keys: se informado, restringe a coleta a esses concorrentes (por key). Se None,
      usa todos os concorrentes ativos em competitors.yaml.

    Retorna a quantidade de registros de price_history gravados.
    """
    _setup_logging()

    session = get_session()
    sync_competitors_from_config(session)

    products_query = session.query(Product).filter(Product.active.is_(True))
    if product_titles:
        lowered = [t.lower() for t in product_titles]
        products_query = products_query.filter(Product.title.in_(product_titles))
        products = [p for p in products_query.all() if p.title.lower() in lowered]
    else:
        products = products_query.all()

    if not products:
        logger.warning("Nenhum produto ativo encontrado para coleta.")
        return 0

    all_competitors = load_competitors(active_only=True)
    if competitor_keys:
        all_competitors = [c for c in all_competitors if c.key in competitor_keys]

    if not all_competitors:
        logger.warning("Nenhum concorrente ativo configurado para coleta.")
        return 0

    logger.info(
        "Iniciando coleta: %d produto(s) x %d concorrente(s)", len(products), len(all_competitors)
    )

    records_written = 0

    # Auto-reparo por IA: so quando ha chave configurada e a opcao esta ligada. Sem chave, o
    # comportamento e o de sempre (nenhuma chamada de IA, nenhum teste extra por concorrente).
    from id_monitor.ai.settings import auto_repair_enabled

    use_ai = auto_repair_enabled()
    if use_ai:
        logger.info("Reparo automatico por IA ligado: cada site recebe uma busca de teste antes da coleta.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        try:
            for competitor_cfg in all_competitors:
                db_competitor = (
                    session.query(Competitor).filter_by(key=competitor_cfg.key).one()
                )

                if use_ai:
                    from id_monitor.ai.mapper import ensure_healthy

                    outcome = ensure_healthy(competitor_cfg, browser, progress=logger.info)
                    competitor_cfg = outcome.cfg
                    if not outcome.usable:
                        logger.warning(
                            "Pulando '%s' nesta coleta: %s", competitor_cfg.key, outcome.message
                        )
                        for product in products:
                            _save_history(
                                session, product, db_competitor,
                                status=CollectionStatus.ERROR, error_message=outcome.message,
                            )
                            records_written += 1
                        continue

                context = new_stealth_context(browser)
                try:
                    scraper_cls = competitor_cfg.load_scraper_class()
                    scraper = scraper_cls(competitor_cfg)
                    page = context.new_page()

                    blocked_message: str | None = None
                    for product in products:
                        if blocked_message is not None:
                            # Ja fomos barrados: nao insiste (piora o bloqueio); registra o motivo.
                            _save_history(
                                session, product, db_competitor,
                                status=CollectionStatus.ERROR, error_message=blocked_message,
                            )
                            records_written += 1
                            continue
                        blocked = _collect_single(
                            session=session,
                            product=product,
                            db_competitor=db_competitor,
                            scraper=scraper,
                            page=page,
                        )
                        records_written += 1
                        if blocked:
                            blocked_message = (
                                session.query(PriceHistory)
                                .filter_by(product_id=product.id, competitor_id=db_competitor.id)
                                .order_by(PriceHistory.id.desc()).first().error_message
                            )
                            logger.warning(
                                "'%s' bloqueou o acesso: pulando os demais livros desse site nesta coleta.",
                                competitor_cfg.key,
                            )
                            continue
                        human_delay()
                except Exception:  # noqa: BLE001 - um concorrente quebrar nao pode parar os outros
                    logger.exception(
                        "Falha inesperada processando o concorrente '%s'", competitor_cfg.key
                    )
                finally:
                    context.close()
        finally:
            browser.close()

    logger.info("Coleta finalizada: %d registro(s) gravados em price_history.", records_written)
    return records_written


def _collect_single(*, session: Session, product: Product, db_competitor: Competitor, scraper, page) -> bool:
    """Coleta o preco de um unico produto em um unico concorrente e grava o resultado.

    Estrategia (ISBN primeiro, para nao pegar edicao/editora errada com o mesmo titulo):
      1. Busca pelo ISBN (digitos, com hifen e ISBN-10, quando der).
      2. Se nao achar, busca pelo titulo (muitos sites nao indexam ISBN).
    Devolve True quando o site BLOQUEOU o acesso (firewall/captcha).
    """
    from id_monitor.matching.isbn import isbn_search_queries

    attempts: list[tuple[str, bool]] = []
    isbn_queries = isbn_search_queries(product.isbn)
    for q in isbn_queries:
        attempts.append((q, True))
    attempts.append((product.title, False))

    match = None
    tried_isbn = False
    for query, by_isbn in attempts:
        if by_isbn:
            tried_isbn = True
            logger.info(
                "Buscando '%s' pelo ISBN '%s' em '%s'...",
                product.title, query, db_competitor.key,
            )
        try:
            candidates = scraper.search(page, query)
        except ScraperBlockedError as exc:
            logger.warning("'%s' bloqueou o acesso ao buscar '%s': %s", db_competitor.key, product.title, exc)
            _save_history(
                session, product, db_competitor,
                status=CollectionStatus.ERROR, error_message=str(exc),
            )
            return True
        except ScraperSearchError as exc:
            logger.warning("Falha ao buscar '%s' em '%s': %s", product.title, db_competitor.key, exc)
            _save_history(
                session, product, db_competitor,
                status=CollectionStatus.ERROR, error_message=str(exc),
            )
            return False
        except Exception as exc:  # noqa: BLE001 - erro inesperado tambem nao deve travar a coleta
            logger.exception("Erro inesperado buscando '%s' em '%s'", product.title, db_competitor.key)
            _save_history(
                session, product, db_competitor,
                status=CollectionStatus.ERROR, error_message=str(exc),
            )
            return False

        match = match_product(
            product.title,
            candidates,
            searched_by_isbn=by_isbn,
            product_isbn=product.isbn,
        )
        if match.status != CollectionStatus.NOT_FOUND and match.raw_result is not None:
            if by_isbn:
                logger.info(
                    "'%s' encontrado pelo ISBN em '%s' (confianca %s).",
                    product.title, db_competitor.key, match.confidence,
                )
            break
        if by_isbn and query == isbn_queries[-1]:
            logger.info(
                "'%s' nao achado pelo ISBN em '%s'; tentando pelo titulo.",
                product.title, db_competitor.key,
            )
        elif by_isbn:
            continue

    if match is None or match.status == CollectionStatus.NOT_FOUND or match.raw_result is None:
        if not tried_isbn and not product.isbn:
            logger.info(
                "'%s' nao tem ISBN cadastrado; a busca foi so pelo titulo (pode confundir edicoes).",
                product.title,
            )
        _save_history(session, product, db_competitor, status=CollectionStatus.NOT_FOUND)
        return False

    _save_history(
        session,
        product,
        db_competitor,
        status=match.status,
        price=match.raw_result.price,
        price_promo=match.raw_result.price_promo,
        availability=match.raw_result.availability,
        product_url=match.raw_result.url,
        match_confidence=match.confidence,
    )
    return False

def _save_history(
    session: Session,
    product: Product,
    competitor: Competitor,
    *,
    status: CollectionStatus,
    price: float | None = None,
    price_promo: float | None = None,
    availability: str | None = None,
    product_url: str | None = None,
    match_confidence: float | None = None,
    error_message: str | None = None,
) -> None:
    entry = PriceHistory(
        product_id=product.id,
        competitor_id=competitor.id,
        collected_at=datetime.utcnow(),
        price=price,
        price_promo=price_promo,
        availability=availability,
        product_url=product_url,
        match_confidence=match_confidence,
        status=status,
        error_message=error_message,
    )
    session.add(entry)
    session.commit()
