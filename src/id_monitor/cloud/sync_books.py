"""Sincroniza livros monitorados entre SQLite local e Firestore."""
from __future__ import annotations

import logging
from typing import Callable

from id_monitor.cloud.firestore_store import (
    CloudStoreError,
    clear_books_remote,
    delete_book_remote,
    list_books_remote,
    upsert_book,
)
from id_monitor.cloud.settings import cloud_configured
from id_monitor.matching.isbn import normalize_isbn_digits
from id_monitor.storage.db import Product, ProductOrigin, find_product_by_isbn, get_session

logger = logging.getLogger("id_monitor.cloud")
Progress = Callable[[str], None]


def push_product(product: Product) -> None:
    try:
        upsert_book(product)
    except CloudStoreError as exc:
        logger.warning("Nao foi possivel enviar livro ao Firestore: %s", exc)


def push_delete(product: Product) -> None:
    try:
        delete_book_remote(product)
    except CloudStoreError as exc:
        logger.warning("Nao foi possivel remover livro remoto: %s", exc)


def push_clear_all() -> None:
    try:
        clear_books_remote()
    except CloudStoreError as exc:
        logger.warning("Nao foi possivel limpar livros remotos: %s", exc)


def pull_books_into_local(progress: Progress | None = None) -> int:
    """Puxa books do Firestore e atualiza/cria no SQLite. Devolve quantos foram tocados."""
    progress = progress or (lambda _m: None)
    if not cloud_configured():
        progress("Nuvem desligada; pulando sync de livros.")
        return 0
    try:
        remote = list_books_remote()
    except CloudStoreError as exc:
        progress(f"Falha ao puxar livros: {exc}")
        return 0

    session = get_session()
    touched = 0
    for item in remote:
        isbn = normalize_isbn_digits(item.get("isbn")) if item.get("isbn") else None
        title = (item.get("title") or "").strip()
        if not title:
            continue
        existing = find_product_by_isbn(session, isbn) if isbn else None
        if existing is None and not isbn:
            existing = (
                session.query(Product)
                .filter(Product.title.ilike(title), Product.isbn.is_(None))
                .first()
            )
        origin_raw = str(item.get("origin") or "manual")
        try:
            origin = ProductOrigin(origin_raw)
        except ValueError:
            origin = ProductOrigin.MANUAL

        if existing is None:
            existing = Product(
                title=title,
                author=item.get("author"),
                isbn=isbn,
                sku=item.get("sku"),
                price_id_livraria=item.get("price_id_livraria"),
                origin=origin,
                active=bool(item.get("active", True)),
            )
            session.add(existing)
        else:
            existing.title = title
            existing.author = item.get("author") or existing.author
            existing.isbn = isbn or existing.isbn
            existing.sku = item.get("sku") or existing.sku
            if item.get("price_id_livraria") is not None:
                existing.price_id_livraria = item.get("price_id_livraria")
            existing.active = bool(item.get("active", True))
        touched += 1
    session.commit()
    progress(f"Sync livros: {touched} item(ns) do Firestore aplicados localmente.")
    return touched
