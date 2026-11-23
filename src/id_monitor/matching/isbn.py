"""Utilitarios de ISBN: normalizacao, variantes de busca e conversao ISBN-13 <-> ISBN-10.

Usados pela coleta para procurar o livro certo (mesmo titulo pode ser edicao/editora diferente)
e pelo matching para confirmar um candidato.
"""
from __future__ import annotations

import re


def normalize_isbn_digits(raw: str | None) -> str | None:
    """Devolve so os digitos (e X do ISBN-10) se for ISBN-10 ou ISBN-13 plausivel."""
    if not raw:
        return None
    digits = re.sub(r"[^0-9Xx]", "", raw).upper()
    if len(digits) == 13 and digits.isdigit():
        return digits
    if len(digits) == 10 and digits[:-1].isdigit() and digits[-1] in "0123456789X":
        return digits
    return None


def isbn13_to_isbn10(isbn13: str) -> str | None:
    """Converte ISBN-13 (978...) para ISBN-10. Livros antigos / alguns sites ainda usam ISBN-10."""
    if len(isbn13) != 13 or not isbn13.startswith("978") or not isbn13.isdigit():
        return None
    body = isbn13[3:12]
    total = sum((10 - i) * int(d) for i, d in enumerate(body))
    check = (11 - (total % 11)) % 11
    return body + ("X" if check == 10 else str(check))


def hyphenate_isbn13(isbn13: str) -> str | None:
    """Formata ISBN-13 no padrao comum brasileiro 978-XX-XXXXX-XXX-X (ex.: 978-85-7341-493-6).

    Nao cobre todos os grupos de registro do mundo; e so uma variante extra para sites que
    indexam o ISBN com hifens.
    """
    if len(isbn13) != 13 or not isbn13.isdigit():
        return None
    # 978/979 + grupo (2) + editor (5) + titulo (3) + digito
    return f"{isbn13[0:3]}-{isbn13[3:5]}-{isbn13[5:10]}-{isbn13[10:12]}-{isbn13[12]}"


def isbn_search_queries(raw: str | None) -> list[str]:
    """Termos de busca a tentar, em ordem: digitos, com hifen, ISBN-10 (se der para converter)."""
    digits = normalize_isbn_digits(raw)
    if not digits:
        return []
    queries: list[str] = [digits]
    if len(digits) == 13:
        hyp = hyphenate_isbn13(digits)
        if hyp:
            queries.append(hyp)
        isbn10 = isbn13_to_isbn10(digits)
        if isbn10:
            queries.append(isbn10)
    # Sem duplicatas, preservando ordem
    seen: set[str] = set()
    unique: list[str] = []
    for q in queries:
        if q not in seen:
            seen.add(q)
            unique.append(q)
    return unique


def text_mentions_isbn(text: str | None, isbn: str | None) -> bool:
    """True se o texto (titulo, URL, etc.) contem o ISBN (com ou sem hifen/espaco)."""
    digits = normalize_isbn_digits(isbn)
    if not digits or not text:
        return False
    haystack = re.sub(r"[^0-9Xx]", "", text).upper()
    return digits in haystack
