"""Fluxo manual de monitoramento: autor, limpar lista, migracao SQLite."""
from __future__ import annotations

import pytest

from id_monitor.storage.db import (
    Product,
    ProductOrigin,
    clear_monitored_books,
    get_session,
    init_db,
)


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setattr("id_monitor.storage.db.DB_PATH", tmp_path / "manual.db")
    monkeypatch.setattr("id_monitor.storage.db._engine", None)
    monkeypatch.setattr("id_monitor.storage.db._SessionFactory", None)
    init_db()


def test_product_stores_author(isolated_db):
    session = get_session()
    session.add(Product(
        title="O Evangelho Segundo o Espiritismo",
        author="Allan Kardec",
        isbn="9788573414950",
        price_id_livraria=23.0,
        origin=ProductOrigin.MANUAL,
        active=True,
    ))
    session.commit()
    book = session.query(Product).one()
    assert book.author == "Allan Kardec"
    assert book.isbn == "9788573414950"


def test_same_title_different_isbn_are_distinct(isolated_db):
    """Edicoes diferentes do mesmo titulo podem coexistir se o ISBN for outro."""
    from id_monitor.storage.db import find_product_by_isbn

    session = get_session()
    session.add(Product(
        title="O Evangelho Segundo o Espiritismo - Edicao Economica",
        author="Allan Kardec",
        isbn="9788594662903",
        price_id_livraria=15.20,
        origin=ProductOrigin.MANUAL,
        active=True,
    ))
    session.commit()

    assert find_product_by_isbn(session, "9788594662903") is not None
    assert find_product_by_isbn(session, "978-85-94662-90-3") is not None
    assert find_product_by_isbn(session, "9788573287547") is None

    session.add(Product(
        title="O Evangelho Segundo o Espiritismo - Nova Traducao",
        author="Allan Kardec",
        isbn="9788573287547",
        price_id_livraria=33.0,
        origin=ProductOrigin.MANUAL,
        active=True,
    ))
    session.commit()
    assert session.query(Product).count() == 2


def test_clear_monitored_books_removes_all(isolated_db):
    session = get_session()
    session.add_all([
        Product(title="A", isbn="9780000000001", origin=ProductOrigin.MANUAL, active=True),
        Product(title="B", isbn="9780000000002", origin=ProductOrigin.CATALOG, active=True),
    ])
    session.commit()
    assert clear_monitored_books() == 2
    assert session.query(Product).count() == 0


def test_migrate_adds_author_column_on_old_db(tmp_path, monkeypatch):
    import sqlite3
    from pathlib import Path

    db_path = tmp_path / "old.db"
    conn = sqlite3.connect(db_path)
    conn.execute(
        "CREATE TABLE products ("
        "id INTEGER PRIMARY KEY, title VARCHAR(512) NOT NULL, isbn VARCHAR(32), "
        "sku VARCHAR(64), price_id_livraria FLOAT, origin VARCHAR(10) NOT NULL, "
        "active BOOLEAN NOT NULL, created_at DATETIME NOT NULL)"
    )
    conn.execute(
        "INSERT INTO products (title, origin, active, created_at) "
        "VALUES ('Livro Velho', 'MANUAL', 1, '2026-01-01')"
    )
    conn.commit()
    conn.close()

    monkeypatch.setattr("id_monitor.storage.db.DB_PATH", db_path)
    monkeypatch.setattr("id_monitor.storage.db._engine", None)
    monkeypatch.setattr("id_monitor.storage.db._SessionFactory", None)
    init_db()

    cols = {row[1] for row in sqlite3.connect(db_path).execute("PRAGMA table_info(products)")}
    assert "author" in cols
    session = get_session()
    book = session.query(Product).one()
    assert book.title == "Livro Velho"
    book.author = "Autor X"
    session.commit()
    assert session.query(Product).one().author == "Autor X"
