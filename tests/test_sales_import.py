"""Testes do import de CSV de vendas e selecao automatica de top N (id_monitor.sales_import)."""
from __future__ import annotations

import pytest

from id_monitor.sales_import import import_sales_csv
from id_monitor.storage.db import Product, ProductOrigin, get_session, init_db


@pytest.fixture
def isolated_db(tmp_path, monkeypatch):
    test_db_path = tmp_path / "test_sales.db"
    monkeypatch.setattr("id_monitor.storage.db.DB_PATH", test_db_path)
    monkeypatch.setattr("id_monitor.storage.db._engine", None)
    monkeypatch.setattr("id_monitor.storage.db._SessionFactory", None)
    init_db()
    return test_db_path


def _write_csv(tmp_path, content: str):
    path = tmp_path / "vendas.csv"
    path.write_text(content, encoding="utf-8")
    return path


def test_import_selects_top_n_most_sold(isolated_db, tmp_path):
    csv_content = (
        "titulo,isbn,quantidade_vendida,preco\n"
        "Livro A,111,50,39.90\n"
        "Livro B,222,10,19.90\n"
        "Livro C,333,30,24.90\n"
    )
    csv_path = _write_csv(tmp_path, csv_content)

    selected = import_sales_csv(csv_path, top_n=2, mode="most_sold")
    titles = {p.title for p in selected}

    assert titles == {"Livro A", "Livro C"}

    session = get_session()
    db_titles = {p.title for p in session.query(Product).filter(Product.active.is_(True)).all()}
    assert db_titles == {"Livro A", "Livro C"}

    livro_a = session.query(Product).filter_by(title="Livro A").one()
    assert livro_a.origin == ProductOrigin.AUTO_SALES
    assert livro_a.price_id_livraria == 39.90


def test_import_least_sold_mode(isolated_db, tmp_path):
    csv_content = (
        "titulo,quantidade_vendida\n"
        "Livro A,50\n"
        "Livro B,10\n"
        "Livro C,30\n"
    )
    csv_path = _write_csv(tmp_path, csv_content)

    selected = import_sales_csv(csv_path, top_n=1, mode="least_sold")
    assert {p.title for p in selected} == {"Livro B"}


def test_reimport_deactivates_previous_auto_sales_not_in_new_list(isolated_db, tmp_path):
    first = _write_csv(
        tmp_path,
        "titulo,quantidade_vendida\nLivro A,50\nLivro B,40\n",
    )
    import_sales_csv(first, top_n=2)

    second_path = tmp_path / "vendas2.csv"
    second_path.write_text("titulo,quantidade_vendida\nLivro C,100\n", encoding="utf-8")
    import_sales_csv(second_path, top_n=1)

    session = get_session()
    active_titles = {p.title for p in session.query(Product).filter(Product.active.is_(True)).all()}
    assert active_titles == {"Livro C"}

    livro_a = session.query(Product).filter_by(title="Livro A").one()
    assert livro_a.active is False


def test_missing_required_column_raises(isolated_db, tmp_path):
    csv_path = _write_csv(tmp_path, "titulo\nLivro A\n")
    with pytest.raises(ValueError):
        import_sales_csv(csv_path)


def test_manual_products_are_not_deactivated_by_reimport(isolated_db, tmp_path):
    session = get_session()
    session.add(Product(title="Livro Manual", origin=ProductOrigin.MANUAL, active=True))
    session.commit()

    csv_path = _write_csv(tmp_path, "titulo,quantidade_vendida\nLivro X,10\n")
    import_sales_csv(csv_path, top_n=1)

    session = get_session()
    manual = session.query(Product).filter_by(title="Livro Manual").one()
    assert manual.active is True
