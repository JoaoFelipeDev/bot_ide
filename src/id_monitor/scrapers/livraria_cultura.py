"""Scraper da Livraria Cultura.

Por padrao usa a busca generica por CSS (ver generic_css.py), configurada em
config/competitors.yaml. Se o site tiver algum comportamento especial (ex.: busca via
autocomplete que nao aceita URL direta, paginacao via JS), sobrescreva `search()` aqui.
"""
from __future__ import annotations

from id_monitor.scrapers.generic_css import GenericCssScraper


class LivrariaCulturaScraper(GenericCssScraper):
    """Scraper piloto do MVP. Usa o comportamento generico ate que uma necessidade especifica
    do site exija logica customizada."""
