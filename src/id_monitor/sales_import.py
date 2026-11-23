"""Importa um CSV de vendas exportado pelo cliente e seleciona automaticamente os top N
titulos (mais ou menos vendidos) para entrar na lista de monitoramento.

Formato esperado do CSV (cabecalho flexivel, nao diferencia maiusculas/acentos):
  titulo (obrigatorio), isbn (opcional), sku (opcional),
  quantidade_vendida (obrigatorio), preco (opcional)

Exemplo:
  titulo,isbn,quantidade_vendida,preco
  "Harry Potter e a Pedra Filosofal",9788532511010,42,39.90
  "O Pequeno Principe",9788574061346,30,24.90
"""
from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path

from id_monitor.storage.db import Product, ProductOrigin, get_session

# Mapeia variacoes comuns de nome de coluna (apos normalizacao) para o campo interno.
_COLUMN_ALIASES = {
    "titulo": "title",
    "title": "title",
    "nome": "title",
    "isbn": "isbn",
    "sku": "sku",
    "codigo": "sku",
    "quantidade": "quantity",
    "quantidadevendida": "quantity",
    "qtdvendida": "quantity",
    "qtd": "quantity",
    "unidadesvendidas": "quantity",
    "preco": "price",
    "precovenda": "price",
    "valor": "price",
}


def _normalize_header(name: str) -> str:
    name = name.strip().lower()
    name = unicodedata.normalize("NFKD", name)
    name = "".join(ch for ch in name if not unicodedata.combining(ch))
    name = re.sub(r"[^a-z0-9]", "", name)
    return name


def _parse_number(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    value = value.replace(".", "").replace(",", ".") if "," in value else value
    try:
        return float(value)
    except ValueError:
        return None


def _read_rows(csv_path: Path) -> list[dict]:
    with csv_path.open("r", encoding="utf-8-sig", newline="") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;")
        except csv.Error:
            dialect = csv.excel

        reader = csv.DictReader(fh, dialect=dialect)
        if not reader.fieldnames:
            raise ValueError(f"CSV '{csv_path}' nao tem cabecalho reconhecivel.")

        field_map = {}
        for raw_name in reader.fieldnames:
            key = _COLUMN_ALIASES.get(_normalize_header(raw_name))
            if key:
                field_map[raw_name] = key

        if "title" not in field_map.values():
            raise ValueError(
                f"CSV '{csv_path}' precisa de uma coluna de titulo (ex.: 'titulo'). "
                f"Colunas encontradas: {reader.fieldnames}"
            )
        if "quantity" not in field_map.values():
            raise ValueError(
                f"CSV '{csv_path}' precisa de uma coluna de quantidade vendida "
                f"(ex.: 'quantidade_vendida'). Colunas encontradas: {reader.fieldnames}"
            )

        rows = []
        for raw_row in reader:
            row = {}
            for raw_name, value in raw_row.items():
                key = field_map.get(raw_name)
                if key:
                    row[key] = value
            rows.append(row)
        return rows


def import_sales_csv(
    csv_path: Path,
    top_n: int = 30,
    mode: str = "most_sold",
) -> list[Product]:
    """Le o CSV de vendas, seleciona os top N titulos (mais vendidos por padrao, ou menos
    vendidos se mode='least_sold') e garante que estejam cadastrados em `products` com
    origin=AUTO_SALES. Produtos auto-selecionados de ciclos anteriores que nao entraram na
    nova lista sao desativados (active=False), para refletir a revisao mensal.

    Retorna a lista de Product (ja persistidos) selecionados neste ciclo.
    """
    csv_path = Path(csv_path)
    if not csv_path.exists():
        raise FileNotFoundError(f"Arquivo de vendas nao encontrado: {csv_path}")

    rows = _read_rows(csv_path)

    parsed = []
    for row in rows:
        title = (row.get("title") or "").strip()
        quantity = _parse_number(row.get("quantity"))
        if not title or quantity is None:
            continue
        parsed.append(
            {
                "title": title,
                "isbn": (row.get("isbn") or "").strip() or None,
                "sku": (row.get("sku") or "").strip() or None,
                "quantity": quantity,
                "price": _parse_number(row.get("price")),
            }
        )

    if not parsed:
        raise ValueError(f"Nenhuma linha valida (titulo + quantidade) encontrada em {csv_path}")

    reverse = mode == "most_sold"
    parsed.sort(key=lambda item: item["quantity"], reverse=reverse)
    selected = parsed[:top_n]

    session = get_session()
    selected_products: list[Product] = []
    selected_titles = {item["title"].strip().lower() for item in selected}

    for item in selected:
        existing = (
            session.query(Product)
            .filter(Product.title.ilike(item["title"]))
            .one_or_none()
        )
        if existing is None:
            existing = Product(
                title=item["title"],
                isbn=item["isbn"],
                sku=item["sku"],
                price_id_livraria=item["price"],
                origin=ProductOrigin.AUTO_SALES,
                active=True,
            )
            session.add(existing)
        else:
            existing.isbn = item["isbn"] or existing.isbn
            existing.sku = item["sku"] or existing.sku
            if item["price"] is not None:
                existing.price_id_livraria = item["price"]
            existing.origin = ProductOrigin.AUTO_SALES
            existing.active = True
        selected_products.append(existing)

    # Desativa produtos auto-selecionados de ciclos anteriores que nao fazem parte da nova lista,
    # mas preserva produtos adicionados manualmente (origin=MANUAL).
    previously_auto = (
        session.query(Product)
        .filter(Product.origin == ProductOrigin.AUTO_SALES, Product.active.is_(True))
        .all()
    )
    for product in previously_auto:
        if product.title.strip().lower() not in selected_titles:
            product.active = False

    session.commit()
    return selected_products
