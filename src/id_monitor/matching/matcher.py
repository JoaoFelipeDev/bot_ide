"""Estrategia de correspondencia entre um livro monitorado e os resultados brutos de busca
em um site concorrente.

Fluxo de uso (ver id_monitor/collector.py):
  1. A busca no site e feita preferencialmente pelo ISBN do produto (quando disponivel), pois
     ISBN e um identificador unico e distingue edicoes/editoras com o mesmo titulo.
  2. Se o candidato traz ISBN (ou o ISBN aparece na URL/titulo) e bate com o nosso, e match certo.
  3. Quando a busca foi feita por ISBN e devolveu exatamente 1 candidato, tratamos como match de
     alta confianca (o site encontrou aquele codigo unico).
  4. Quando a busca por ISBN devolveu varios candidatos (site "bagunca" a busca), so aceitamos
     se o titulo bater; senao caimos para a busca por titulo.
  5. Busca por titulo: match exato; se houver varios com o mesmo titulo e temos ISBN, marcamos
     incerto (nao arriscamos a edicao errada). Fallback fuzzy com limiar.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

from id_monitor.matching.isbn import normalize_isbn_digits, text_mentions_isbn
from id_monitor.scrapers.base import RawResult
from id_monitor.storage.db import CollectionStatus

# Limiar minimo (0-100) de similaridade fuzzy de titulo para aceitar um match automatico.
FUZZY_MATCH_THRESHOLD = 85
# Diferenca minima de score entre o melhor e o segundo melhor candidato para considerar o
# melhor como nao-ambiguo quando o match nao foi exato.
FUZZY_AMBIGUITY_MARGIN = 5


@dataclass
class MatchResult:
    """Resultado da correspondencia entre um produto monitorado e os candidatos encontrados."""

    raw_result: RawResult | None
    status: CollectionStatus
    confidence: float | None  # 0-100, ou None quando nao se aplica (ex.: NOT_FOUND)


def normalize_title(text: str) -> str:
    """Normaliza um titulo para comparacao: minusculas, sem acento, sem pontuacao, espacos unicos."""
    text = text.strip().lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9 ]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _candidate_has_isbn(candidate: RawResult, product_isbn: str) -> bool:
    own = normalize_isbn_digits(getattr(candidate, "isbn", None))
    target = normalize_isbn_digits(product_isbn)
    if own and target and own == target:
        return True
    return text_mentions_isbn(candidate.url, product_isbn) or text_mentions_isbn(
        candidate.title, product_isbn
    )


def match_product(
    product_title: str,
    candidates: list[RawResult],
    *,
    searched_by_isbn: bool = False,
    product_isbn: str | None = None,
) -> MatchResult:
    """Decide qual (se algum) candidato corresponde ao produto monitorado.

    `searched_by_isbn=True` indica que a busca no site foi feita usando o ISBN do produto.
    `product_isbn` e o ISBN do livro monitorado (quando existe) e tem prioridade maxima.
    """
    if not candidates:
        return MatchResult(raw_result=None, status=CollectionStatus.NOT_FOUND, confidence=None)

    isbn = normalize_isbn_digits(product_isbn)

    # 0) Confirmacao por ISBN no proprio resultado (campo, URL ou titulo) — o mais seguro.
    if isbn:
        isbn_hits = [c for c in candidates if _candidate_has_isbn(c, isbn)]
        if len(isbn_hits) == 1:
            return MatchResult(raw_result=isbn_hits[0], status=CollectionStatus.FOUND, confidence=100.0)
        if len(isbn_hits) > 1:
            # Mesmo ISBN em varios anuncios: escolhe pelo titulo, se der.
            title_hits = [c for c in isbn_hits if normalize_title(c.title) == normalize_title(product_title)]
            if len(title_hits) == 1:
                return MatchResult(raw_result=title_hits[0], status=CollectionStatus.FOUND, confidence=100.0)
            return MatchResult(
                raw_result=isbn_hits[0], status=CollectionStatus.UNCERTAIN, confidence=92.0
            )

    # 1) Busca por ISBN com resultado unico: alta confianca.
    if searched_by_isbn and len(candidates) == 1:
        return MatchResult(raw_result=candidates[0], status=CollectionStatus.FOUND, confidence=100.0)

    target_norm = normalize_title(product_title)

    # 2) Match exato de titulo normalizado.
    exact_matches = [c for c in candidates if normalize_title(c.title) == target_norm]
    if len(exact_matches) == 1:
        return MatchResult(raw_result=exact_matches[0], status=CollectionStatus.FOUND, confidence=100.0)
    if len(exact_matches) > 1:
        # Varias edicoes com o mesmo titulo. Sem ISBN no resultado, nao arriscamos a errada.
        if isbn and not searched_by_isbn:
            return MatchResult(
                raw_result=None, status=CollectionStatus.NOT_FOUND, confidence=None
            )
        return MatchResult(
            raw_result=exact_matches[0], status=CollectionStatus.UNCERTAIN, confidence=90.0
        )

    # Busca por ISBN que devolveu varios resultados sem titulo certo: o site nao indexou bem
    # o ISBN (ex.: Curitiba devolvendo 40 livros aleatorios). Nao escolhemos por fuzzy aqui —
    # o collector vai tentar de novo pelo titulo.
    if searched_by_isbn and len(candidates) > 1:
        return MatchResult(raw_result=None, status=CollectionStatus.NOT_FOUND, confidence=None)

    # 3) Fallback fuzzy: pontua cada candidato por similaridade de titulo.
    scored = sorted(
        (
            (candidate, fuzz.token_sort_ratio(target_norm, normalize_title(candidate.title)))
            for candidate in candidates
        ),
        key=lambda pair: pair[1],
        reverse=True,
    )

    best_candidate, best_score = scored[0]
    second_score = scored[1][1] if len(scored) > 1 else 0

    if best_score < FUZZY_MATCH_THRESHOLD:
        return MatchResult(raw_result=None, status=CollectionStatus.NOT_FOUND, confidence=best_score)

    if (best_score - second_score) < FUZZY_AMBIGUITY_MARGIN:
        return MatchResult(
            raw_result=best_candidate, status=CollectionStatus.UNCERTAIN, confidence=best_score
        )

    return MatchResult(raw_result=best_candidate, status=CollectionStatus.FOUND, confidence=best_score)
