"""Testes da visao 'ultimos resultados' (id_monitor.reporting.results)."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from id_monitor.config import load_competitors
from id_monitor.reporting.results import effective_price, latest_results, summarize
from id_monitor.storage.db import (
    CollectionStatus,
    Competitor,
    PriceHistory,
    Product,
    ProductOrigin,
    get_session,
    init_db,
    sync_competitors_from_config,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr("id_monitor.storage.db.DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr("id_monitor.storage.db._engine", None)
    monkeypatch.setattr("id_monitor.storage.db._SessionFactory", None)
    init_db()
    session = get_session()
    sync_competitors_from_config(session)
    return session


def _add(session, product, comp_key, *, status=CollectionStatus.FOUND, when=None, **kw):
    comp = session.query(Competitor).filter_by(key=comp_key).one()
    entry = PriceHistory(
        product_id=product.id, competitor_id=comp.id, status=status,
        collected_at=when or datetime.utcnow(), **kw,
    )
    session.add(entry)
    session.commit()
    return entry


def _two_active_keys():
    keys = [c.key for c in load_competitors(active_only=True)]
    assert len(keys) >= 2
    return keys[0], keys[1]


def test_effective_price_prefers_promo():
    assert effective_price(50.0, 40.0) == 40.0
    assert effective_price(50.0, None) == 50.0
    assert effective_price(50.0, 0) == 50.0
    assert effective_price(None, None) is None


def test_rows_cover_every_product_and_active_competitor(db):
    book = Product(title="Livro A", isbn="9788573414936", price_id_livraria=30.0,
                   origin=ProductOrigin.MANUAL, active=True)
    db.add(book)
    db.add(Product(title="Desativado", origin=ProductOrigin.MANUAL, active=False))
    db.commit()

    rows = latest_results(db)
    competitors = load_competitors(active_only=True)
    assert len(rows) == len(competitors)  # so o livro ativo
    assert {r.status_label for r in rows} == {"sem coleta"}
    assert "Ainda nao ha coleta" in summarize(rows)


def test_latest_entry_wins_and_compares_with_ide_price(db):
    key_a, key_b = _two_active_keys()
    book = Product(title="Livro A", price_id_livraria=30.0, origin=ProductOrigin.MANUAL, active=True)
    db.add(book)
    db.commit()
    old = datetime.utcnow() - timedelta(days=3)
    _add(db, book, key_a, when=old, price=10.0)  # antigo, deve perder
    _add(db, book, key_a, price=50.0, price_promo=25.0, product_url="https://x/livro", match_confidence=0.9)
    _add(db, book, key_b, price=30.0)

    rows = {r.competitor_key: r for r in latest_results(db)}

    a = rows[key_a]
    assert a.price == 25.0 and a.price_regular == 50.0  # promocao vale
    assert a.diff == 5.0 and a.verdict == "ide_cara"
    assert "seu preco mais caro" in a.diff_text and "R$ 5,00" in a.diff_text
    assert a.url == "https://x/livro" and a.status_label == "ok"

    b = rows[key_b]
    assert b.verdict == "igual" and b.diff_text == "igual"


def test_errors_and_not_found_have_no_verdict(db):
    key_a, key_b = _two_active_keys()
    book = Product(title="Livro A", price_id_livraria=30.0, origin=ProductOrigin.MANUAL, active=True)
    db.add(book)
    db.commit()
    _add(db, book, key_a, status=CollectionStatus.ERROR, error_message="site fora do ar")
    _add(db, book, key_b, status=CollectionStatus.NOT_FOUND)

    rows = {r.competitor_key: r for r in latest_results(db)}
    assert rows[key_a].status_label == "erro na coleta" and rows[key_a].error == "site fora do ar"
    assert rows[key_b].status_label == "nao encontrado"
    assert rows[key_a].verdict == "" and rows[key_a].diff is None
    summary = summarize(list(rows.values()))
    assert "erro na coleta" in summary and "nao encontrado" in summary


def test_ide_cheaper_and_missing_ide_price(db):
    key_a, key_b = _two_active_keys()
    cheap = Product(title="Barato", price_id_livraria=10.0, origin=ProductOrigin.MANUAL, active=True)
    noprice = Product(title="Sem preco IDE", origin=ProductOrigin.MANUAL, active=True)
    db.add_all([cheap, noprice])
    db.commit()
    _add(db, cheap, key_a, price=20.0)
    _add(db, noprice, key_a, price=20.0)

    rows = {(r.title, r.competitor_key): r for r in latest_results(db)}
    assert rows[("Barato", key_a)].verdict == "ide_barata"
    assert "seu preco mais barato" in rows[("Barato", key_a)].diff_text
    assert rows[("Sem preco IDE", key_a)].verdict == ""


# ------------------------------------------------- busca por ISBN com fallback por titulo
class _FakeScraper:
    """Responde so para o titulo (como Boa Nova/FEB, que nao indexam ISBN na busca)."""

    def __init__(self):
        self.queries = []

    def search(self, page, query):
        from id_monitor.scrapers.base import RawResult

        self.queries.append(query)
        if query == "A Casa Assombrada":
            return [RawResult(title="A Casa Assombrada", price=60.0, price_promo=54.0,
                              url="https://x/casa", availability="em estoque")]
        return []


def test_collect_single_falls_back_to_title_when_isbn_finds_nothing(db):
    from id_monitor.collector import _collect_single

    book = Product(title="A Casa Assombrada", isbn="9786586112337", price_id_livraria=54.0,
                   origin=ProductOrigin.MANUAL, active=True)
    db.add(book)
    db.commit()
    comp = db.query(Competitor).first()
    scraper = _FakeScraper()

    _collect_single(session=db, product=book, db_competitor=comp, scraper=scraper, page=None)

    assert scraper.queries[0] == "9786586112337"
    assert scraper.queries[-1] == "A Casa Assombrada"
    assert "9786586112337" in scraper.queries and "A Casa Assombrada" in scraper.queries
    entry = db.query(PriceHistory).one()
    assert entry.status == CollectionStatus.FOUND
    assert entry.price_promo == 54.0 and entry.product_url == "https://x/casa"


def test_collect_single_not_found_after_both_attempts(db):
    from id_monitor.collector import _collect_single

    book = Product(title="Livro Inexistente", isbn="9780000000002", origin=ProductOrigin.MANUAL, active=True)
    db.add(book)
    db.commit()
    scraper = _FakeScraper()

    _collect_single(session=db, product=book, db_competitor=db.query(Competitor).first(),
                    scraper=scraper, page=None)

    assert scraper.queries[0] == "9780000000002"
    assert scraper.queries[-1] == "Livro Inexistente"
    assert db.query(PriceHistory).one().status == CollectionStatus.NOT_FOUND


def test_collect_single_accepts_isbn_hit_without_falling_back_to_title(db):
    from id_monitor.collector import _collect_single
    from id_monitor.scrapers.base import RawResult

    class IsbnScraper:
        def __init__(self):
            self.queries = []

        def search(self, page, query):
            self.queries.append(query)
            if query == "9786586112337":
                return [RawResult(title="A Casa Assombrada", price=51.0, url="https://x/9786586112337")]
            raise AssertionError(f"nao deveria buscar por {query!r}")

    book = Product(title="A Casa Assombrada", isbn="9786586112337", origin=ProductOrigin.MANUAL, active=True)
    db.add(book)
    db.commit()
    scraper = IsbnScraper()
    _collect_single(session=db, product=book, db_competitor=db.query(Competitor).first(),
                    scraper=scraper, page=None)
    assert scraper.queries == ["9786586112337"]
    assert db.query(PriceHistory).one().status == CollectionStatus.FOUND
