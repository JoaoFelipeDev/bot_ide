"""Importa o catalogo da IDE Editora (titulo, autor, ISBN e preco) direto do Firestore do projeto
`ide-digital`, o mesmo que alimenta a loja (ide-reader-shop) e o painel admin.

Acesso: a colecao `e-books` tem leitura publica nas regras do Firestore (`allow read: if true`),
entao usamos a API REST **sem nenhuma credencial**. Este modulo nunca le `.env.local` nem a chave
de service account do painel, e so faz leitura.

Atencao: o catalogo da loja e de e-books. A colecao `books` (livros fisicos) existe no legado,
mas esta vazia e nao e publica; por isso os precos importados sao os do e-book.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from id_monitor.config import DATA_DIR
from id_monitor.storage.db import Product, ProductOrigin, get_session

FIRESTORE_PROJECT_ID = "ide-digital"
FIRESTORE_COLLECTION = "e-books"
_BASE = "https://firestore.googleapis.com/v1/projects/{project}/databases/(default)/documents/{col}"

# Itens abaixo disso costumam ser brindes/amostras (ex.: R$ 2,00, R$ 3,00), nao livros de venda.
DEFAULT_MIN_PRICE = 5.0
REQUEST_TIMEOUT_S = 30


class CatalogError(RuntimeError):
    """Falha ao ler o catalogo (sem internet, regras mudaram, etc.)."""


@dataclass
class CatalogItem:
    doc_id: str
    title: str
    author: str
    isbn: str | None
    list_price: float
    discount_pct: float
    active: bool

    @property
    def final_price(self) -> float:
        """Preco que o cliente paga: preco de tabela menos o desconto (mesma conta do app)."""
        return round(self.list_price * (1 - self.discount_pct / 100), 2)


@dataclass
class ImportReport:
    added: list[Product] = field(default_factory=list)
    updated: list[Product] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (titulo, motivo)

    @property
    def total_changed(self) -> int:
        return len(self.added) + len(self.updated)


# --------------------------------------------------------------------------- leitura
def normalize_isbn(raw: str | None) -> str | None:
    """Devolve so os digitos se for um ISBN-10 ou ISBN-13 plausivel; senao None."""
    from id_monitor.matching.isbn import normalize_isbn_digits
    return normalize_isbn_digits(raw)


def _field(fields: dict, name: str):
    value = fields.get(name) or {}
    for kind in ("stringValue", "integerValue", "doubleValue", "booleanValue"):
        if kind in value:
            return value[kind]
    return None


def _to_float(value) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return 0.0


def parse_document(doc: dict) -> CatalogItem | None:
    fields = doc.get("fields") or {}
    title = str(_field(fields, "name") or "").strip()
    if not title:
        return None
    return CatalogItem(
        doc_id=doc.get("name", "").rsplit("/", 1)[-1],
        title=title,
        author=str(_field(fields, "author") or "").strip(),
        isbn=normalize_isbn(_field(fields, "isbn")),
        list_price=_to_float(_field(fields, "price")),
        discount_pct=_to_float(_field(fields, "discount")),
        active=bool(_field(fields, "active")),
    )


def fetch_catalog(
    project_id: str = FIRESTORE_PROJECT_ID, collection: str = FIRESTORE_COLLECTION
) -> list[CatalogItem]:
    """Baixa todos os documentos da colecao (paginado) e devolve os itens validos."""
    base = _BASE.format(project=project_id, col=urllib.parse.quote(collection, safe=""))
    items: list[CatalogItem] = []
    page_token: str | None = None
    while True:
        params = {"pageSize": "300"}
        if page_token:
            params["pageToken"] = page_token
        url = f"{base}?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=REQUEST_TIMEOUT_S) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise CatalogError(
                f"O Firestore recusou a leitura do catalogo (HTTP {exc.code}). "
                "As regras de seguranca podem ter mudado."
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise CatalogError(f"Nao consegui acessar o catalogo da IDE (sem internet?): {exc}") from exc

        for doc in payload.get("documents", []):
            item = parse_document(doc)
            if item is not None:
                items.append(item)
        page_token = payload.get("nextPageToken")
        if not page_token:
            return items


# --------------------------------------------------------------------------- importacao
def select_items(
    items: list[CatalogItem], min_price: float = DEFAULT_MIN_PRICE
) -> tuple[list[CatalogItem], list[tuple[str, str]]]:
    """Filtra inativos, itens simbolicos e duplicados. Devolve (aceitos, [(titulo, motivo)])."""
    accepted: list[CatalogItem] = []
    skipped: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in items:
        if not item.active:
            skipped.append((item.title, "inativo no catalogo"))
            continue
        if item.list_price < min_price:
            skipped.append((item.title, f"preco simbolico (R$ {item.list_price:.2f})"))
            continue
        key = item.isbn or item.title.strip().lower()
        if key in seen:
            skipped.append((item.title, "duplicado"))
            continue
        seen.add(key)
        accepted.append(item)
    return accepted, skipped


def _find_product(session, item: CatalogItem) -> Product | None:
    """Casa pelo ISBN. Titulo so preenche livro ja cadastrado SEM ISBN (nao mistura edicoes)."""
    from id_monitor.storage.db import find_product_by_isbn

    if item.isbn:
        found = find_product_by_isbn(session, item.isbn)
        if found is not None:
            return found
        return (
            session.query(Product)
            .filter(Product.isbn.is_(None), Product.title.ilike(item.title))
            .first()
        )
    return session.query(Product).filter(Product.title.ilike(item.title)).first()


def import_catalog(
    items: list[CatalogItem],
    *,
    add_new: bool = True,
    use_final_price: bool = True,
    min_price: float = DEFAULT_MIN_PRICE,
    reactivate: bool = False,
) -> ImportReport:
    """Grava o catalogo em `products`.

    * `add_new=True`: cadastra os livros que ainda nao estao monitorados (origin=catalog).
    * `add_new=False`: so atualiza preco/ISBN de quem ja esta cadastrado (nao cria nada).
    * `use_final_price=True` usa o preco ja com desconto (o que o cliente paga na loja).
    * `reactivate=True` (escolha explicita do usuario) religa livros que estavam desativados.
    Nunca muda a origem de livros existentes; sem `reactivate`, respeita livros desativados.
    """
    accepted, skipped = select_items(items, min_price=min_price)
    report = ImportReport(skipped=skipped)
    session = get_session()

    for item in accepted:
        price = item.final_price if use_final_price else item.list_price
        product = _find_product(session, item)
        if product is None:
            if not add_new:
                report.skipped.append((item.title, "nao esta na lista de monitoramento"))
                continue
            product = Product(
                title=item.title,
                author=item.author or None,
                isbn=item.isbn,
                price_id_livraria=price,
                origin=ProductOrigin.CATALOG,
                active=True,
            )
            session.add(product)
            report.added.append(product)
        else:
            product.price_id_livraria = price
            if item.isbn and not product.isbn:
                product.isbn = item.isbn
            if item.author and not getattr(product, "author", None):
                product.author = item.author
            if reactivate:
                product.active = True
            report.updated.append(product)

    session.commit()
    return report


# --------------------------------------------------------------------------- uso na interface
def cache_path() -> Path:
    return DATA_DIR / "catalog_cache.json"


def save_cache(items: list[CatalogItem]) -> None:
    """Guarda uma copia local para a aba "Catalogo IDE" abrir na hora, mesmo sem internet."""
    path = cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "items": [asdict(item) for item in items],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_cache() -> tuple[list[CatalogItem], str | None]:
    """Le a copia local. Devolve ([], None) se nao existir ou estiver ilegivel."""
    try:
        payload = json.loads(cache_path().read_text(encoding="utf-8"))
        items = [CatalogItem(**raw) for raw in payload["items"]]
        return items, payload.get("fetched_at")
    except (OSError, ValueError, KeyError, TypeError):
        return [], None


def refresh_catalog(sync_prices: bool = True) -> tuple[list[CatalogItem], ImportReport | None]:
    """Baixa o catalogo, atualiza a copia local e (opcional) os precos dos livros ja monitorados.

    Nao adiciona livros novos: quem decide o que monitorar e o usuario (ver `add_items`).
    """
    items = fetch_catalog()
    save_cache(items)
    report = import_catalog(items, add_new=False) if sync_prices else None
    return items, report


def monitored_flags(items: list[CatalogItem]) -> dict[str, bool]:
    """doc_id -> True se o livro ja esta cadastrado E ativo na lista de monitoramento."""
    session = get_session()
    flags: dict[str, bool] = {}
    for item in items:
        product = _find_product(session, item)
        flags[item.doc_id] = bool(product is not None and product.active)
    return flags


def add_items(items: list[CatalogItem]) -> ImportReport:
    """Adiciona os livros escolhidos pelo usuario a lista de monitoramento (reativa se preciso)."""
    return import_catalog(items, add_new=True, min_price=0.0, reactivate=True)


def stop_monitoring(items: list[CatalogItem]) -> int:
    """Desativa (nao apaga, o historico fica) os livros escolhidos. Devolve quantos mudaram."""
    session = get_session()
    changed = 0
    for item in items:
        product = _find_product(session, item)
        if product is not None and product.active:
            product.active = False
            changed += 1
    session.commit()
    return changed
