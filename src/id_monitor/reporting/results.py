"""Visao "ultimos resultados" da coleta: um registro por (livro ativo x concorrente ativo), usando
o snapshot mais recente de cada par. E a base da aba "Resultados" da interface e do relatorio.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from id_monitor.config import load_competitors
from id_monitor.storage.db import Competitor, CollectionStatus, PriceHistory, Product, get_session

STATUS_LABELS = {
    CollectionStatus.FOUND: "ok",
    CollectionStatus.UNCERTAIN: "incerto",
    CollectionStatus.NOT_FOUND: "nao encontrado",
    CollectionStatus.ERROR: "erro na coleta",
}
NO_COLLECTION_LABEL = "sem coleta"
CENT_TOLERANCE = 0.005


def effective_price(price: float | None, price_promo: float | None) -> float | None:
    """Preco que o cliente realmente paga no concorrente: a promocao, quando existe."""
    if price_promo is not None and price_promo > 0:
        return price_promo
    return price


@dataclass
class ResultRow:
    product_id: int
    title: str
    isbn: str
    competitor_key: str
    competitor_name: str
    ide_price: float | None
    status_label: str
    price: float | None  # preco efetivo no concorrente
    price_regular: float | None  # preco cheio, quando ha promocao
    availability: str
    url: str
    confidence: float | None
    error: str
    collected_at: datetime | None  # horario local

    @property
    def diff(self) -> float | None:
        """IDE - concorrente. Positivo = a IDE esta mais cara."""
        if self.ide_price is None or self.price is None:
            return None
        return round(self.ide_price - self.price, 2)

    @property
    def verdict(self) -> str:
        """'ide_cara', 'ide_barata', 'igual' ou '' quando nao da para comparar."""
        diff = self.diff
        if diff is None:
            return ""
        if abs(diff) < CENT_TOLERANCE:
            return "igual"
        return "ide_cara" if diff > 0 else "ide_barata"

    @property
    def diff_text(self) -> str:
        diff = self.diff
        if diff is None:
            return ""
        verdict = self.verdict
        if verdict == "igual":
            return "igual"
        text = f"{'+' if diff > 0 else '-'}R$ {abs(diff):.2f}".replace(".", ",")
        return f"{text} (seu preco mais {'caro' if diff > 0 else 'barato'})"


def _to_local(value: datetime) -> datetime:
    """`collected_at` e gravado em UTC (naive); mostramos no horario do computador."""
    return value.replace(tzinfo=timezone.utc).astimezone().replace(tzinfo=None)


def latest_entries(session: Session) -> dict[tuple[int, int], PriceHistory]:
    """Para cada (product_id, competitor_id), o registro mais recente de price_history."""
    latest: dict[tuple[int, int], PriceHistory] = {}
    for entry in session.execute(select(PriceHistory)).scalars().all():
        key = (entry.product_id, entry.competitor_id)
        current = latest.get(key)
        if current is None or (entry.collected_at, entry.id) > (current.collected_at, current.id):
            latest[key] = entry
    return latest


def latest_results(session: Session | None = None) -> list[ResultRow]:
    """Linhas (livro ativo x concorrente ativo) com o ultimo resultado de cada par."""
    session = session or get_session()
    products = session.query(Product).filter(Product.active.is_(True)).order_by(Product.title).all()
    competitors = load_competitors(active_only=True)
    db_by_key = {c.key: c for c in session.query(Competitor).all()}
    latest = latest_entries(session)

    rows: list[ResultRow] = []
    for product in products:
        for cfg in competitors:
            db_comp = db_by_key.get(cfg.key)
            entry = latest.get((product.id, db_comp.id)) if db_comp else None
            base = dict(
                product_id=product.id, title=product.title, isbn=product.isbn or "",
                competitor_key=cfg.key, competitor_name=cfg.name, ide_price=product.price_id_livraria,
            )
            if entry is None:
                rows.append(ResultRow(
                    **base, status_label=NO_COLLECTION_LABEL, price=None, price_regular=None,
                    availability="", url="", confidence=None, error="", collected_at=None,
                ))
                continue
            promo_applies = entry.price_promo is not None and entry.price_promo > 0
            rows.append(ResultRow(
                **base,
                status_label=STATUS_LABELS.get(entry.status, str(entry.status)),
                price=effective_price(entry.price, entry.price_promo),
                price_regular=entry.price if promo_applies else None,
                availability=entry.availability or "",
                url=entry.product_url or "",
                confidence=entry.match_confidence,
                error=entry.error_message or "",
                collected_at=_to_local(entry.collected_at),
            ))
    return rows


def summarize(rows: list[ResultRow]) -> str:
    """Resumo de uma linha para o topo da tela."""
    collected = [r.collected_at for r in rows if r.collected_at]
    if not rows:
        return "Nenhum livro ativo ou nenhum concorrente ativo."
    if not collected:
        return "Ainda nao ha coleta. Clique em 'Coletar tudo' ou 'Coleta avulsa...'."
    counts: dict[str, int] = {}
    for row in rows:
        counts[row.status_label] = counts.get(row.status_label, 0) + 1
    parts = ", ".join(f"{n} {label}" for label, n in counts.items())
    when = max(collected).strftime("%d/%m/%Y %H:%M")
    return f"Ultima coleta: {when}  |  {len(rows)} linha(s): {parts}"
