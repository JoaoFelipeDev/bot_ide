"""Testes dos utilitarios de ISBN (id_monitor.matching.isbn)."""
from id_monitor.matching.isbn import (
    hyphenate_isbn13,
    isbn13_to_isbn10,
    isbn_search_queries,
    normalize_isbn_digits,
    text_mentions_isbn,
)


def test_normalize_isbn_digits():
    assert normalize_isbn_digits("978-85-7341-493-6") == "9788573414936"
    assert normalize_isbn_digits("9788573414936") == "9788573414936"
    assert normalize_isbn_digits("85-7341-301-8") == "8573413018"
    assert normalize_isbn_digits("") is None
    assert normalize_isbn_digits("123") is None


def test_isbn13_to_isbn10_and_hyphen():
    assert isbn13_to_isbn10("9788573414936") == "8573414936"
    hyp = hyphenate_isbn13("9788573414936")
    assert hyp == "978-85-73414-93-6"
    assert hyp.replace("-", "") == "9788573414936"


def test_isbn_search_queries_order():
    qs = isbn_search_queries("978-85-7341-493-6")
    assert qs[0] == "9788573414936"
    assert "978-85-73414-93-6" in qs
    assert "8573414936" in qs


def test_text_mentions_isbn():
    assert text_mentions_isbn("https://loja/produto/978-85-7341-493-6", "9788573414936")
    assert text_mentions_isbn("ISBN 9788573414936 capa dura", "978-85-7341-493-6")
    assert not text_mentions_isbn("https://loja/outro", "9788573414936")
