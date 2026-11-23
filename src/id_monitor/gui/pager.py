"""Barra de paginacao reutilizavel para as listas (Treeview) da interface."""
from __future__ import annotations

import math
import tkinter as tk
from tkinter import ttk
from typing import Callable, Sequence, TypeVar

T = TypeVar("T")

PAGE_SIZES = (5, 10, 20, 50, 100)
DEFAULT_PAGE_SIZE = 20


def page_count(total: int, page_size: int) -> int:
    """Quantidade de paginas (minimo 1, mesmo com lista vazia)."""
    return max(1, math.ceil(total / page_size)) if page_size > 0 else 1


def slice_page(items: Sequence[T], page: int, page_size: int) -> tuple[list[T], int]:
    """Devolve (itens da pagina, pagina ajustada ao intervalo valido, base 0)."""
    last = page_count(len(items), page_size) - 1
    page = min(max(page, 0), last)
    start = page * page_size
    return list(items[start:start + page_size]), page


class PagerBar(ttk.Frame):
    """Seletor de tamanho de pagina (5/10/20/50/100) + botoes de navegacao + 'Pagina X de Y'.

    Uso: a tela guarda a lista ja filtrada e chama `pager.paginate(lista)` ao desenhar; o
    `on_change` e chamado quando o usuario troca de pagina ou de tamanho.
    """

    def __init__(self, parent: tk.Widget, on_change: Callable[[], None], page_size: int = DEFAULT_PAGE_SIZE) -> None:
        super().__init__(parent)
        self._on_change = on_change
        self.page = 0
        self.total = 0
        self.size_var = tk.StringVar(value=str(page_size))
        self.info_var = tk.StringVar(value="")

        ttk.Label(self, text="Itens por pagina:").pack(side="left")
        box = ttk.Combobox(
            self, textvariable=self.size_var, values=[str(s) for s in PAGE_SIZES], width=5, state="readonly"
        )
        box.pack(side="left", padx=(4, 12))
        box.bind("<<ComboboxSelected>>", lambda _e: self._size_changed())

        self.first_btn = ttk.Button(self, text="|<", width=3, command=lambda: self._go(0))
        self.prev_btn = ttk.Button(self, text="< Anterior", command=lambda: self._go(self.page - 1))
        self.next_btn = ttk.Button(self, text="Proxima >", command=lambda: self._go(self.page + 1))
        self.last_btn = ttk.Button(self, text=">|", width=3, command=lambda: self._go(10**9))
        for button in (self.first_btn, self.prev_btn):
            button.pack(side="left", padx=2)
        ttk.Label(self, textvariable=self.info_var, width=34, anchor="center").pack(side="left", padx=6)
        for button in (self.next_btn, self.last_btn):
            button.pack(side="left", padx=2)

    @property
    def page_size(self) -> int:
        try:
            return max(1, int(self.size_var.get()))
        except ValueError:
            return DEFAULT_PAGE_SIZE

    def reset(self) -> None:
        """Volta para a primeira pagina (use quando o filtro mudar)."""
        self.page = 0

    def paginate(self, items: Sequence[T]) -> list[T]:
        """Recorta `items` na pagina atual e atualiza o texto e os botoes."""
        self.total = len(items)
        visible, self.page = slice_page(items, self.page, self.page_size)
        pages = page_count(self.total, self.page_size)
        if self.total:
            first = self.page * self.page_size + 1
            last = first + len(visible) - 1
            self.info_var.set(f"{first}-{last} de {self.total}  (pagina {self.page + 1}/{pages})")
        else:
            self.info_var.set("0 itens")
        at_start, at_end = self.page == 0, self.page >= pages - 1
        for button, disabled in (
            (self.first_btn, at_start), (self.prev_btn, at_start),
            (self.next_btn, at_end), (self.last_btn, at_end),
        ):
            button.state(["disabled"] if disabled else ["!disabled"])
        return visible

    def _go(self, page: int) -> None:
        self.page = page
        self._on_change()

    def _size_changed(self) -> None:
        self.page = 0
        self._on_change()
