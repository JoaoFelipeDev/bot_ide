"""Testes unitarios da estrategia de matching (id_monitor.matching.matcher)."""
from __future__ import annotations

from id_monitor.matching.matcher import match_product, normalize_title
from id_monitor.scrapers.base import RawResult
from id_monitor.storage.db import CollectionStatus


def _candidate(title: str, price: float = 10.0) -> RawResult:
    return RawResult(title=title, price=price, url="https://example.com")


def test_normalize_title_strips_accents_case_and_punctuation():
    assert normalize_title("O Pequeno Príncipe!!") == "o pequeno principe"
    assert normalize_title("  Múltiplos   Espaços  ") == "multiplos espacos"


def test_no_candidates_is_not_found():
    result = match_product("Qualquer Titulo", [])
    assert result.status == CollectionStatus.NOT_FOUND
    assert result.raw_result is None


def test_single_candidate_from_isbn_search_is_high_confidence_found():
    candidates = [_candidate("Harry Potter e a Pedra Filosofal")]
    result = match_product("Nome Diferente No Nosso Catalogo", candidates, searched_by_isbn=True)
    assert result.status == CollectionStatus.FOUND
    assert result.confidence == 100.0


def test_exact_title_match_is_found():
    candidates = [_candidate("Harry Potter e a Pedra Filosofal"), _candidate("Outro Livro")]
    result = match_product("harry potter e a pedra filosofal", candidates)
    assert result.status == CollectionStatus.FOUND
    assert result.raw_result.title == "Harry Potter e a Pedra Filosofal"


def test_fuzzy_match_above_threshold_is_found():
    candidates = [_candidate("Harry Poter e a Pedra Filosofal"), _candidate("Totalmente Diferente")]
    result = match_product("Harry Potter e a Pedra Filosofal", candidates)
    assert result.status == CollectionStatus.FOUND
    assert result.raw_result.title == "Harry Poter e a Pedra Filosofal"


def test_low_similarity_is_not_found():
    candidates = [_candidate("Um Livro Completamente Nao Relacionado")]
    result = match_product("Harry Potter e a Pedra Filosofal", candidates)
    assert result.status == CollectionStatus.NOT_FOUND


def test_ambiguous_close_scores_marked_uncertain():
    candidates = [_candidate("Harry Potter e a Camara Secreta"), _candidate("Harry Potter e o Prisioneiro")]
    result = match_product("Harry Potter e a Pedra Filosofal", candidates)
    assert result.status in (CollectionStatus.UNCERTAIN, CollectionStatus.NOT_FOUND)


def test_isbn_in_url_wins_over_same_title_editions():
    candidates = [
        RawResult(title="A Genese", price=40.0, url="https://loja/genese-outra-editora"),
        RawResult(title="A Genese", price=23.0, url="https://loja/9788573414936"),
    ]
    result = match_product("A Genese", candidates, product_isbn="978-85-7341-493-6")
    assert result.status == CollectionStatus.FOUND
    assert result.raw_result.price == 23.0
    assert "9788573414936" in result.raw_result.url


def test_isbn_field_on_candidate_is_preferred():
    candidates = [
        RawResult(title="A Genese", price=99.0, isbn="9780000000000"),
        RawResult(title="Qualquer", price=23.0, isbn="9788573414936"),
    ]
    result = match_product("A Genese", candidates, product_isbn="9788573414936")
    assert result.status == CollectionStatus.FOUND
    assert result.raw_result.price == 23.0


def test_multiple_same_titles_with_isbn_are_not_guessed_on_title_search():
    candidates = [_candidate("A Genese", 40.0), _candidate("A Genese", 23.0)]
    result = match_product("A Genese", candidates, product_isbn="9788573414936")
    assert result.status == CollectionStatus.NOT_FOUND
    assert result.raw_result is None


def test_isbn_search_with_junk_results_does_not_fuzzy_match():
    """Site devolveu 40 livros aleatorios para a busca por ISBN: nao escolhemos por fuzzy."""
    candidates = [
        _candidate("A Garota Do Lago"),
        _candidate("Flores Para Algernon"),
        _candidate("O Pequeno Principe"),
    ]
    result = match_product(
        "A Casa Assombrada", candidates, searched_by_isbn=True, product_isbn="9786586112337"
    )
    assert result.status == CollectionStatus.NOT_FOUND
