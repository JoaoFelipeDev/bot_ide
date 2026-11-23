"""Testes da importacao do catalogo da IDE (id_monitor.catalog_import) - sem rede."""
from __future__ import annotations

import json

import pytest

from id_monitor import catalog_import
from id_monitor.catalog_import import (
    CatalogError,
    fetch_catalog,
    import_catalog,
    normalize_isbn,
    parse_document,
    select_items,
)
from id_monitor.storage.db import Product, ProductOrigin, get_session, init_db


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr("id_monitor.storage.db.DB_PATH", tmp_path / "test_catalog.db")
    monkeypatch.setattr("id_monitor.storage.db._engine", None)
    monkeypatch.setattr("id_monitor.storage.db._SessionFactory", None)
    init_db()


def _doc(name, price="40.00", discount=10, isbn="978-85-7341-493-6", active=True, doc_id="abc"):
    fields = {
        "name": {"stringValue": name},
        "author": {"stringValue": "Autor"},
        "price": {"stringValue": price},
        "discount": {"integerValue": str(discount)},
        "active": {"booleanValue": active},
    }
    if isbn is not None:
        fields["isbn"] = {"stringValue": isbn}
    return {"name": f"projects/p/databases/(default)/documents/e-books/{doc_id}", "fields": fields}


def test_normalize_isbn():
    assert normalize_isbn("978-85-7341-493-6") == "9788573414936"
    assert normalize_isbn("85-7341-301-8") == "8573413018"
    assert normalize_isbn("978-85-7341-237-602-2") is None  # digitos demais
    assert normalize_isbn("") is None
    assert normalize_isbn(None) is None


def test_parse_document_and_final_price():
    item = parse_document(_doc("A Genese", price="23.00", discount=10))
    assert item.title == "A Genese"
    assert item.isbn == "9788573414936"
    assert item.list_price == 23.0
    assert item.final_price == 20.70
    assert item.doc_id == "abc"


def test_parse_document_without_name_is_ignored():
    assert parse_document({"name": "x/y", "fields": {}}) is None


def test_select_items_filters_inactive_symbolic_and_duplicates():
    items = [
        parse_document(_doc("Bom", doc_id="1")),
        parse_document(_doc("Inativo", active=False, isbn="978-85-7341-111-1", doc_id="2")),
        parse_document(_doc("Brinde", price="2.00", isbn=None, doc_id="3")),
        parse_document(_doc("Bom (copia)", doc_id="4")),  # mesmo ISBN do "Bom"
    ]
    accepted, skipped = select_items(items)
    assert [i.title for i in accepted] == ["Bom"]
    reasons = dict(skipped)
    assert "inativo" in reasons["Inativo"]
    assert "simbolico" in reasons["Brinde"]
    assert reasons["Bom (copia)"] == "duplicado"


def test_import_adds_new_books_with_final_price_and_catalog_origin(isolated_db):
    items = [parse_document(_doc("A Genese", price="23.00", discount=10))]
    report = import_catalog(items)

    assert len(report.added) == 1
    product = get_session().query(Product).one()
    assert product.origin == ProductOrigin.CATALOG
    assert product.isbn == "9788573414936"
    assert product.price_id_livraria == 20.70
    assert product.active is True


def test_import_list_price_option(isolated_db):
    import_catalog([parse_document(_doc("A Genese", price="23.00", discount=10))], use_final_price=False)
    assert get_session().query(Product).one().price_id_livraria == 23.0


def test_only_update_does_not_create_and_keeps_user_choices(isolated_db):
    session = get_session()
    session.add(Product(title="A Genese", origin=ProductOrigin.MANUAL, active=False))
    session.commit()

    items = [
        parse_document(_doc("A Genese", price="23.00", discount=10)),
        parse_document(_doc("Outro", isbn="978-85-7341-222-2", doc_id="2")),
    ]
    report = import_catalog(items, add_new=False)

    assert len(report.updated) == 1 and not report.added
    session = get_session()
    assert session.query(Product).count() == 1
    product = session.query(Product).one()
    assert product.price_id_livraria == 20.70
    assert product.isbn == "9788573414936"  # preencheu o ISBN que faltava
    assert product.origin == ProductOrigin.MANUAL  # nao mudou a origem
    assert product.active is False  # respeitou o livro desativado


def test_reimport_matches_by_isbn_and_does_not_duplicate(isolated_db):
    import_catalog([parse_document(_doc("A Genese", price="23.00"))])
    import_catalog([parse_document(_doc("A Gênese (novo titulo)", price="30.00", discount=0))])
    session = get_session()
    assert session.query(Product).count() == 1
    assert session.query(Product).one().price_id_livraria == 30.0


def test_fetch_catalog_paginates(monkeypatch):
    pages = {
        None: {"documents": [_doc("Livro 1", doc_id="1")], "nextPageToken": "t2"},
        "t2": {"documents": [_doc("Livro 2", isbn="978-85-7341-222-2", doc_id="2")]},
    }

    class FakeResp:
        def __init__(self, payload):
            self._data = json.dumps(payload).encode("utf-8")

        def read(self):
            return self._data

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def fake_urlopen(url, timeout=None):
        token = "t2" if "pageToken=t2" in url else None
        return FakeResp(pages[token])

    monkeypatch.setattr(catalog_import.urllib.request, "urlopen", fake_urlopen)
    assert [i.title for i in fetch_catalog()] == ["Livro 1", "Livro 2"]


def test_fetch_catalog_network_error_is_friendly(monkeypatch):
    def boom(url, timeout=None):
        raise catalog_import.urllib.error.URLError("sem rede")

    monkeypatch.setattr(catalog_import.urllib.request, "urlopen", boom)
    with pytest.raises(CatalogError):
        fetch_catalog()

# ----------------------------------------------------------------- uso pela interface
@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    monkeypatch.setattr("id_monitor.catalog_import.DATA_DIR", tmp_path)


def test_cache_roundtrip(isolated_cache):
    items = [parse_document(_doc("A Genese", price="23.00", discount=10))]
    catalog_import.save_cache(items)
    loaded, fetched_at = catalog_import.load_cache()
    assert loaded == items
    assert fetched_at


def test_load_cache_missing_or_corrupt_returns_empty(isolated_cache, tmp_path):
    assert catalog_import.load_cache() == ([], None)
    (tmp_path / "catalog_cache.json").write_text("{nao e json", encoding="utf-8")
    assert catalog_import.load_cache() == ([], None)


def test_add_items_reactivates_and_flags(isolated_db):
    item = parse_document(_doc("A Genese", price="23.00", discount=10))
    assert catalog_import.monitored_flags([item]) == {"abc": False}

    catalog_import.add_items([item])
    assert catalog_import.monitored_flags([item]) == {"abc": True}

    assert catalog_import.stop_monitoring([item]) == 1
    assert catalog_import.monitored_flags([item]) == {"abc": False}
    assert catalog_import.stop_monitoring([item]) == 0  # ja estava desativado

    catalog_import.add_items([item])  # escolha explicita religa o livro
    session = get_session()
    assert session.query(Product).count() == 1
    assert session.query(Product).one().active is True


def test_add_items_accepts_cheap_books_chosen_by_user(isolated_db):
    cheap = parse_document(_doc("Livreto", price="3.00", isbn=None))
    catalog_import.add_items([cheap])
    assert get_session().query(Product).one().price_id_livraria == 2.70


def test_refresh_catalog_updates_prices_but_never_adds(isolated_db, isolated_cache, monkeypatch):
    item = parse_document(_doc("A Genese", price="23.00", discount=10))
    catalog_import.add_items([item])
    new_prices = [
        parse_document(_doc("A Genese", price="30.00", discount=0)),
        parse_document(_doc("Outro", isbn="978-85-7341-222-2", doc_id="2")),
    ]
    monkeypatch.setattr(catalog_import, "fetch_catalog", lambda: new_prices)

    items, report = catalog_import.refresh_catalog()

    assert len(items) == 2
    session = get_session()
    assert session.query(Product).count() == 1  # "Outro" nao foi adicionado sozinho
    assert session.query(Product).one().price_id_livraria == 30.0
    assert len(catalog_import.load_cache()[0]) == 2
