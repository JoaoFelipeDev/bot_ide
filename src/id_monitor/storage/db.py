"""Modelos e sessao do banco SQLite (products, competitors, price_history).

O banco guarda:
- competitors: cadastro dos sites concorrentes monitorados (espelha config/competitors.yaml,
  mas tambem serve para referenciar nas FKs de price_history).
- products: itens monitorados do catalogo da IDE Livraria (manual ou via import de vendas).
- price_history: cada coleta de preco de um produto em um concorrente, em uma data.
"""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from id_monitor.config import DB_PATH, ensure_directories


class Base(DeclarativeBase):
    pass


class ProductOrigin(str, enum.Enum):
    MANUAL = "manual"
    AUTO_SALES = "auto_sales"
    CATALOG = "catalog"


class CollectionStatus(str, enum.Enum):
    FOUND = "found"
    NOT_FOUND = "not_found"
    UNCERTAIN = "uncertain"
    ERROR = "error"


class Competitor(Base):
    """Um site concorrente monitorado."""

    __tablename__ = "competitors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    base_url: Mapped[str] = mapped_column(String(256), nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    price_entries: Mapped[list["PriceHistory"]] = relationship(
        back_populates="competitor", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - utilitario de debug
        return f"<Competitor key={self.key!r} name={self.name!r}>"


class Product(Base):
    """Um livro que voce escolheu monitorar (manual, CSV ou atalho do catalogo IDE).

    Nao precisa ser livro da IDE: a loja tambem revende titulos de outras editoras.
    O campo `price_id_livraria` guarda o *seu* preco de referencia para comparar.
    """

    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    author: Mapped[str | None] = mapped_column(String(256), nullable=True)
    isbn: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True)
    price_id_livraria: Mapped[float | None] = mapped_column(Float, nullable=True)
    origin: Mapped[ProductOrigin] = mapped_column(
        Enum(ProductOrigin), default=ProductOrigin.MANUAL, nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    price_entries: Mapped[list["PriceHistory"]] = relationship(
        back_populates="product", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:  # pragma: no cover - utilitario de debug
        return f"<Product title={self.title!r} isbn={self.isbn!r}>"


class PriceHistory(Base):
    """Um registro de coleta: preco de um produto em um concorrente, em uma data."""

    __tablename__ = "price_history"
    __table_args__ = (
        UniqueConstraint(
            "product_id", "competitor_id", "collected_at", name="uq_price_history_snapshot"
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    competitor_id: Mapped[int] = mapped_column(ForeignKey("competitors.id"), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    price: Mapped[float | None] = mapped_column(Float, nullable=True)
    price_promo: Mapped[float | None] = mapped_column(Float, nullable=True)
    availability: Mapped[str | None] = mapped_column(String(64), nullable=True)
    product_url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    match_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[CollectionStatus] = mapped_column(Enum(CollectionStatus), nullable=False)
    error_message: Mapped[str | None] = mapped_column(String(1024), nullable=True)

    product: Mapped[Product] = relationship(back_populates="price_entries")
    competitor: Mapped[Competitor] = relationship(back_populates="price_entries")

    def __repr__(self) -> str:  # pragma: no cover - utilitario de debug
        return (
            f"<PriceHistory product_id={self.product_id} competitor_id={self.competitor_id} "
            f"status={self.status} price={self.price}>"
        )


_engine = None
_SessionFactory: sessionmaker | None = None


def get_engine():
    """Retorna (criando se necessario) a engine SQLAlchemy apontando para data/monitor.db."""
    global _engine
    if _engine is None:
        ensure_directories()
        _engine = create_engine(f"sqlite:///{DB_PATH}", echo=False, future=True)
    return _engine


def init_db() -> None:
    """Cria as tabelas no banco, caso ainda nao existam, e aplica migracoes leves do SQLite."""
    Base.metadata.create_all(get_engine())
    _migrate_sqlite_schema()


def _migrate_sqlite_schema() -> None:
    """Adiciona colunas novas em bancos antigos (SQLite nao altera sozinho com create_all)."""
    from sqlalchemy import inspect, text

    engine = get_engine()
    inspector = inspect(engine)
    if "products" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("products")}
    with engine.begin() as conn:
        if "author" not in columns:
            conn.execute(text("ALTER TABLE products ADD COLUMN author VARCHAR(256)"))


def clear_monitored_books(*, delete_history: bool = True) -> int:
    """Remove todos os livros da lista de monitoramento. Devolve quantos foram apagados.

    Por padrao tambem apaga o historico de precos deles (cascade). Use quando o cliente
    quiser comecar do zero e alimentar so os titulos que importa.
    """
    session = get_session()
    products = session.query(Product).all()
    count = len(products)
    for product in products:
        if delete_history:
            for entry in list(product.price_entries):
                session.delete(entry)
        session.delete(product)
    session.commit()
    return count


def get_session() -> Session:
    """Abre uma nova sessao SQLAlchemy. O chamador e responsavel por fechar/usar como context manager."""
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(), expire_on_commit=False, future=True)
    return _SessionFactory()


def sync_competitors_from_config(session: Session) -> None:
    """Garante que cada concorrente definido em config/competitors.yaml tenha uma linha na tabela
    `competitors`, criando ou atualizando nome/base_url/active conforme necessario."""
    from id_monitor.config import load_competitors

    for cfg in load_competitors(active_only=False):
        existing = session.query(Competitor).filter_by(key=cfg.key).one_or_none()
        if existing is None:
            session.add(
                Competitor(key=cfg.key, name=cfg.name, base_url=cfg.base_url, active=cfg.active)
            )
        else:
            existing.name = cfg.name
            existing.base_url = cfg.base_url
            existing.active = cfg.active
    session.commit()
