"""Reduz o HTML de uma pagina antes de mandar para a IA, para gastar poucos tokens.

O que faz:
  - remove script/style/svg/iframe/etc. e elementos escondidos;
  - mantem so atributos uteis para montar seletores CSS (id, class, href, data-testid...);
  - trunca textos longos;
  - colapsa listas repetitivas (ex.: 40 cards de produto -> mostra 3 + um aviso), que e onde
    estaria a maior parte do tamanho - sem perder a estrutura que a IA precisa ver;
  - se mesmo assim passar do limite, reduz mais e, em ultimo caso, corta no limite.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

_VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}
_DROP_TAGS = {
    "script", "style", "svg", "noscript", "iframe", "template", "head", "link", "meta",
    "picture", "video", "audio", "canvas", "object", "embed", "map",
}
_LAYOUT_TAGS = {"header", "footer", "nav", "aside"}
_KEEP_ATTRS = {
    "id", "class", "href", "name", "type", "placeholder", "role", "aria-label", "itemprop",
    "itemtype", "action", "method", "title", "alt", "for", "value",
}
_DATA_ATTR_RE = re.compile(r"^data-(test|qa|cy|product|item|sku|price|title|name|id)", re.I)
_PRICE_HINT_RE = re.compile(r"R\$\s*\d")


@dataclass
class _Node:
    tag: str
    attrs: dict[str, str] = field(default_factory=dict)
    children: list["_Node | str"] = field(default_factory=list)


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self._stack: list[_Node] = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {k: (v or "") for k, v in attrs})
        self._stack[-1].children.append(node)
        if tag not in _VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        node = _Node(tag, {k: (v or "") for k, v in attrs})
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID_TAGS:
            return
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return
        # end tag sem abertura correspondente: ignora

    def handle_data(self, data: str) -> None:
        if data.strip():
            self._stack[-1].children.append(data)


def _is_hidden(node: _Node) -> bool:
    if "hidden" in node.attrs:
        return True
    style = node.attrs.get("style", "").replace(" ", "").lower()
    return "display:none" in style or "visibility:hidden" in style


def _clean_attrs(node: _Node, max_href: int = 90, max_attr: int = 120) -> str:
    parts: list[str] = []
    for key, value in node.attrs.items():
        key_l = key.lower()
        if key_l in _KEEP_ATTRS or (_DATA_ATTR_RE.match(key_l) and len(value) <= 40):
            if not value and key_l not in {"hidden"}:
                continue
            limit = max_href if key_l == "href" else max_attr
            value = value.strip()
            if len(value) > limit:
                value = value[:limit] + "..."
            parts.append(f'{key_l}="{value}"')
    return (" " + " ".join(parts)) if parts else ""


def _similar_key(node: "_Node | str") -> tuple | None:
    if isinstance(node, str):
        return None
    return (node.tag, node.attrs.get("class", ""))


def _render(
    node: _Node,
    *,
    keep_similar: int,
    max_text: int,
    drop_layout: bool,
    out: list[str],
) -> None:
    kept: list["_Node | str"] = []
    for child in node.children:
        if isinstance(child, str):
            kept.append(child)
            continue
        if child.tag in _DROP_TAGS or _is_hidden(child):
            continue
        if drop_layout and child.tag in _LAYOUT_TAGS:
            continue
        kept.append(child)

    # Colapsa grupos consecutivos de irmaos "iguais" (mesma tag + mesma classe).
    index = 0
    while index < len(kept):
        child = kept[index]
        if isinstance(child, str):
            text = re.sub(r"\s+", " ", child).strip()
            if text:
                out.append(text if len(text) <= max_text else text[:max_text] + "...")
            index += 1
            continue

        key = _similar_key(child)
        run_end = index
        while run_end + 1 < len(kept) and _similar_key(kept[run_end + 1]) == key:
            run_end += 1
        group = kept[index : run_end + 1]

        for item in group[:keep_similar]:
            _render_element(item, keep_similar=keep_similar, max_text=max_text, drop_layout=drop_layout, out=out)  # type: ignore[arg-type]
        omitted = len(group) - keep_similar
        if omitted > 0:
            out.append(f"<!-- +{omitted} elementos iguais omitidos -->")
        index = run_end + 1


def _render_element(node: _Node, *, keep_similar: int, max_text: int, drop_layout: bool, out: list[str]) -> None:
    attrs = _clean_attrs(node)
    if node.tag in _VOID_TAGS:
        out.append(f"<{node.tag}{attrs}>")
        return
    out.append(f"<{node.tag}{attrs}>")
    _render(node, keep_similar=keep_similar, max_text=max_text, drop_layout=drop_layout, out=out)
    out.append(f"</{node.tag}>")


_PRICE_TEXT_RE = re.compile(r"R\$\s*\d|\d{1,3}(?:\.\d{3})*,\d{2}\b")


def _count_prices(node: _Node, counts: dict[int, int]) -> int:
    total = 0
    for child in node.children:
        if isinstance(child, str):
            if _PRICE_TEXT_RE.search(child):
                total += 1
        elif child.tag not in _DROP_TAGS and not _is_hidden(child):
            total += _count_prices(child, counts)
    counts[id(node)] = total
    return total


def _find_price_container(root: _Node) -> tuple[_Node, list[_Node]]:
    """Desce a arvore ate o menor elemento que ainda contem (quase) todos os precos da pagina:
    e a area dos resultados, sem menu, cabecalho, rodape e vitrines laterais. Retorna esse
    elemento e a cadeia de ancestrais (para dar contexto a IA). Sem precos -> a propria raiz."""
    counts: dict[int, int] = {}
    total = _count_prices(root, counts)
    path: list[_Node] = []
    current = root
    if total == 0:
        return current, path
    while True:
        best = None
        for child in current.children:
            if isinstance(child, _Node) and counts.get(id(child), 0) >= 0.7 * total:
                best = child
                break
        if best is None:
            return current, path
        path.append(best)
        current = best


def _describe_path(path: list[_Node]) -> str:
    parts = []
    for node in path[:-1] if path else []:
        label = node.tag
        if node.attrs.get("id"):
            label += "#" + node.attrs["id"]
        if node.attrs.get("class"):
            label += "." + ".".join(node.attrs["class"].split())
        parts.append(label)
    return " > ".join(parts)


def trim_html(
    html: str,
    max_chars: int = 24_000,
    *,
    drop_layout: bool = True,
    focus_prices: bool = False,
) -> str:
    """Devolve uma versao enxuta do HTML, com no maximo ~max_chars caracteres.

    drop_layout=True remove header/footer/nav/aside (bom para paginas de resultado de busca);
    use False quando precisar enxergar o campo de busca no cabecalho (home).
    focus_prices=True mantem so a area da pagina onde estao os precos (a lista de resultados),
    descartando menus e cabecalhos que, em muitos sites, nao usam as tags <header>/<nav>.
    """
    builder = _TreeBuilder()
    try:
        builder.feed(html)
        builder.close()
    except Exception:  # noqa: BLE001 - HTML malformado nao pode derrubar o mapeamento
        pass

    container, path = (builder.root, [])
    prefix = ""
    if focus_prices:
        container, path = _find_price_container(builder.root)
        if path:
            prefix = f"<!-- area dos resultados dentro de: {_describe_path(path)} -->"

    # Tenta configuracoes progressivamente mais agressivas ate caber no limite.
    for keep_similar, max_text in ((3, 120), (2, 80), (1, 60)):
        out: list[str] = [prefix] if prefix else []
        if container is builder.root:
            _render(container, keep_similar=keep_similar, max_text=max_text, drop_layout=drop_layout, out=out)
        else:
            _render_element(container, keep_similar=keep_similar, max_text=max_text, drop_layout=False, out=out)
        result = "".join(out)
        if len(result) <= max_chars:
            return result

    return result[:max_chars] + "<!-- cortado no limite de tamanho -->"


def looks_like_it_has_prices(html: str) -> bool:
    """Checagem barata: a pagina parece listar precos em reais?"""
    return bool(_PRICE_HINT_RE.search(html))
