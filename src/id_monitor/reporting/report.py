"""Gera o relatorio Excel comparando o preco da IDE Livraria com cada concorrente, por titulo,
usando o snapshot de coleta mais recente de cada (produto, concorrente).
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlalchemy import select
from sqlalchemy.orm import Session

from id_monitor.config import REPORTS_DIR, ensure_directories, load_competitors
from id_monitor.reporting.results import effective_price
from id_monitor.storage.db import Competitor, CollectionStatus, PriceHistory, Product, get_session

GREEN_FILL = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
RED_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
YELLOW_FILL = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
GRAY_FILL = PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")

STATUS_LABELS = {
    CollectionStatus.FOUND: "ok",
    CollectionStatus.UNCERTAIN: "incerto",
    CollectionStatus.NOT_FOUND: "nao encontrado",
    CollectionStatus.ERROR: "erro na coleta",
}


def _latest_price_by_product_and_competitor(session: Session) -> dict[tuple[int, int], PriceHistory]:
    """Retorna, para cada par (product_id, competitor_id), o registro de price_history mais
    recente (maior collected_at)."""
    all_entries = session.execute(select(PriceHistory)).scalars().all()
    latest: dict[tuple[int, int], PriceHistory] = {}
    for entry in all_entries:
        key = (entry.product_id, entry.competitor_id)
        current = latest.get(key)
        if current is None or entry.collected_at > current.collected_at:
            latest[key] = entry
    return latest


def build_report(output_path: Path | None = None) -> Path:
    """Monta o relatorio Excel comparativo e salva em `output_path` (ou em reports/ com nome
    com a data de hoje, se nao informado). Retorna o caminho do arquivo gerado."""
    ensure_directories()
    session = get_session()

    products = session.query(Product).filter(Product.active.is_(True)).order_by(Product.title).all()
    competitors = load_competitors(active_only=True)
    latest = _latest_price_by_product_and_competitor(session)

    rows = []
    for product in products:
        row: dict[str, object] = {
            "Titulo": product.title,
            "Autor": getattr(product, "author", None) or "",
            "ISBN": product.isbn or "",
            "Seu preco": product.price_id_livraria,
        }
        for comp_cfg in competitors:
            db_comp = session.query(Competitor).filter_by(key=comp_cfg.key).one_or_none()
            entry = latest.get((product.id, db_comp.id)) if db_comp else None

            if entry is None:
                row[f"{comp_cfg.name} - Preco"] = None
                row[f"{comp_cfg.name} - Status"] = "sem coleta"
                continue

            row[f"{comp_cfg.name} - Preco"] = effective_price(entry.price, entry.price_promo)
            row[f"{comp_cfg.name} - Status"] = STATUS_LABELS.get(entry.status, entry.status)

        rows.append(row)

    df = pd.DataFrame(rows)

    if output_path is None:
        today = datetime.now().strftime("%Y-%m-%d")
        output_path = REPORTS_DIR / f"relatorio_precos_{today}.xlsx"

    with pd.ExcelWriter(output_path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Comparativo", index=False)
        _apply_formatting(writer, df, competitors)

    return output_path


def _apply_formatting(writer: pd.ExcelWriter, df: pd.DataFrame, competitors) -> None:
    """Aplica negrito no cabecalho, largura de coluna automatica e cores indicando se a
    IDE Livraria esta mais cara, mais barata ou no mesmo preco que cada concorrente."""
    worksheet = writer.sheets["Comparativo"]

    for col_idx, column_name in enumerate(df.columns, start=1):
        cell = worksheet.cell(row=1, column=col_idx)
        cell.font = Font(bold=True)
        max_len = max([len(str(column_name))] + [len(str(v)) for v in df[column_name].tolist()])
        worksheet.column_dimensions[get_column_letter(col_idx)].width = min(max_len + 2, 40)

    own_price_col = df.columns.get_loc("Seu preco") + 1  # 1-indexed para openpyxl

    for comp_cfg in competitors:
        price_col_name = f"{comp_cfg.name} - Preco"
        if price_col_name not in df.columns:
            continue
        price_col_idx = df.columns.get_loc(price_col_name) + 1

        for row_idx in range(2, len(df) + 2):
            own_cell = worksheet.cell(row=row_idx, column=own_price_col)
            comp_cell = worksheet.cell(row=row_idx, column=price_col_idx)
            own_price = own_cell.value
            comp_price = comp_cell.value

            if own_price in (None, "") or comp_price in (None, ""):
                comp_cell.fill = GRAY_FILL
                continue

            if own_price > comp_price:
                comp_cell.fill = RED_FILL  # IDE Livraria mais cara que o concorrente
            elif own_price < comp_price:
                comp_cell.fill = GREEN_FILL  # IDE Livraria mais barata que o concorrente
            else:
                comp_cell.fill = YELLOW_FILL  # preco igual
