"""Interface grafica (tkinter) do Monitor de Precos IDE Livraria.

Roda como um programa comum (sem precisar de terminal): abas para gerenciar os livros
monitorados e os concorrentes, botoes de acao (coletar, gerar relatorio, smoke-test) e um
painel de log mostrando o progresso em tempo real.

Operacoes longas (coleta, smoke-test) rodam numa thread separada para a janela nao travar.
"""
from __future__ import annotations

import logging
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from datetime import datetime
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Any, Callable

from id_monitor.ai.secrets import AIConfigError, delete_api_key, get_api_key, has_api_key, mask_key, set_api_key
from id_monitor.ai.settings import SUGGESTED_MODELS, AISettings, load_settings, save_settings
from id_monitor.catalog_import import (
    CatalogItem,
    add_items,
    load_cache,
    monitored_flags,
    refresh_catalog,
    select_items,
    stop_monitoring,
)
from id_monitor.collector import collect_prices
from id_monitor.config import (
    REPORTS_DIR,
    CompetitorConfig,
    add_competitor,
    get_competitor,
    load_competitors,
    restore_last_recipe_backup,
    update_competitor,
)
from id_monitor.gui.pager import PagerBar
from id_monitor.gui.dialogs import (
    ask_book_fields,
    ask_competitor_fields,
    ask_import_sales_options,
    ask_new_competitor,
    ask_single_book_title,
)
from id_monitor.reporting.report import build_report
from id_monitor.reporting.results import ResultRow, latest_results
from id_monitor.reporting.results import summarize as summarize_results
from id_monitor.sales_import import import_sales_csv
from id_monitor.smoke_test import run_smoke_test
from id_monitor.storage.db import Product, ProductOrigin, clear_monitored_books, get_session, init_db, sync_competitors_from_config


def competitor_status_text(competitor: CompetitorConfig) -> str:
    """Texto da coluna "Status" da aba Concorrentes."""
    if competitor.status == "fora_do_ar":
        return "fora do ar"
    if not competitor.has_recipe:
        return "precisa mapear"
    if competitor.mapped_by_ai and competitor.mapped_at:
        try:
            when = datetime.fromisoformat(competitor.mapped_at).strftime("%d/%m/%Y")
        except ValueError:
            when = competitor.mapped_at
        return f"mapeado por IA em {when}"
    return "ok (manual)"


class _QueueLogHandler(logging.Handler):
    """Handler de logging que empurra cada mensagem formatada numa queue, para ser lida pela
    thread da interface grafica (nunca se deve tocar em widgets tkinter de outra thread)."""

    def __init__(self, log_queue: "queue.Queue[str]") -> None:
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.log_queue.put(self.format(record))
        except Exception:  # noqa: BLE001 - logging nunca deve derrubar a aplicacao
            pass


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Monitor de Precos de Concorrentes - IDE Livraria")
        self.geometry("1000x680")
        self.minsize(860, 560)

        init_db()
        session = get_session()
        sync_competitors_from_config(session)

        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._current_thread: threading.Thread | None = None
        self._task_result: dict[str, Any] = {}
        self._task_on_success: Callable[[Any], None] | None = None
        self._action_buttons: list[tk.Widget] = []

        self._build_layout()
        self.refresh_books()
        self.refresh_competitors()

        self.after(150, self._poll_log_queue)
        self.after(700, self._notify_pending_mapping)
        # Catalogo IDE e so atalho opcional: nao baixa sozinho na abertura (evita sugerir
        # que a lista de monitoramento vem daqui). O usuario atualiza na aba se quiser.
        self.after(2500, self._startup_update_check)
        self.protocol("WM_DELETE_WINDOW", self.destroy)

    # ------------------------------------------------------------------ layout
    def _build_layout(self) -> None:
        notebook = ttk.Notebook(self)
        notebook.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.notebook = notebook

        books_frame = ttk.Frame(notebook)
        catalog_frame = ttk.Frame(notebook)
        results_frame = ttk.Frame(notebook)
        competitors_frame = ttk.Frame(notebook)
        ai_frame = ttk.Frame(notebook)
        notebook.add(books_frame, text="Livros monitorados")
        notebook.add(catalog_frame, text="Atalho catalogo IDE (opcional)")
        notebook.add(results_frame, text="Resultados")
        self._results_tab = results_frame
        notebook.add(competitors_frame, text="Concorrentes")
        notebook.add(ai_frame, text="Configuracoes de IA")
        self._ai_tab = ai_frame
        self._catalog_tab = catalog_frame

        self._build_books_tab(books_frame)
        self._build_catalog_tab(catalog_frame)
        self._build_results_tab(results_frame)
        notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)
        self._build_competitors_tab(competitors_frame)
        self._build_ai_tab(ai_frame)

        actions = ttk.Frame(self)
        actions.pack(fill="x", padx=8, pady=4)

        self._add_action_button(actions, "Coletar tudo", self._on_collect_all)
        self._add_action_button(actions, "Coleta avulsa...", self._on_collect_one)
        self._add_action_button(actions, "Gerar relatorio", self._on_report)
        self._add_action_button(actions, "Abrir pasta de relatorios", self._on_open_reports)
        self._add_action_button(actions, "Smoke-test", self._on_smoke_test)
        self._add_action_button(actions, "Verificar atualizacoes", self._on_check_update)

        self.status_var = tk.StringVar(value="Pronto.")
        ttk.Label(self, textvariable=self.status_var, padding=(8, 0)).pack(fill="x")

        log_frame = ttk.LabelFrame(self, text="Log de atividades")
        log_frame.pack(fill="both", expand=False, padx=8, pady=(4, 8))
        self.log_text = scrolledtext.ScrolledText(log_frame, height=11, state="disabled", wrap="word")
        self.log_text.pack(fill="both", expand=True, padx=4, pady=4)

    def _add_action_button(self, parent: tk.Widget, label: str, command: Callable[[], None]) -> None:
        button = ttk.Button(parent, text=label, command=command)
        button.pack(side="left", padx=4)
        self._action_buttons.append(button)

    # ------------------------------------------------------------- aba livros
    def _build_books_tab(self, parent: ttk.Frame) -> None:
        ttk.Label(
            parent,
            text="Cadastre aqui os livros que voce quer acompanhar (da IDE ou de outras editoras). "
                 "ISBN e o mais importante para achar a edicao certa.",
            foreground="#555555",
            wraplength=920,
            justify="left",
        ).pack(fill="x", padx=8, pady=(8, 0))

        columns = ("title", "author", "isbn", "price", "origin", "active")
        headers = {
            "title": "Titulo", "author": "Autor", "isbn": "ISBN",
            "price": "Seu preco", "origin": "Origem", "active": "Ativo",
        }
        widths = {"title": 220, "author": 160, "isbn": 120, "price": 90, "origin": 90, "active": 60}

        self.books_tree = ttk.Treeview(parent, columns=columns, show="headings", height=14)
        for col in columns:
            self.books_tree.heading(col, text=headers[col])
            self.books_tree.column(col, width=widths[col], anchor="w", stretch=(col == "title"))
        self.books_tree.pack(fill="both", expand=True, padx=8, pady=8)
        self.books_tree.bind("<Double-1>", lambda _evt: self._on_edit_book())

        self.books_pager = PagerBar(parent, on_change=self.refresh_books)
        self.books_pager.pack(fill="x", padx=8)

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", padx=8, pady=(4, 4))
        ttk.Button(buttons, text="Novo livro...", command=self._on_new_book).pack(side="left", padx=4)
        ttk.Button(buttons, text="Editar...", command=self._on_edit_book).pack(side="left", padx=4)
        ttk.Button(buttons, text="Ativar/Desativar", command=self._on_toggle_book_active).pack(side="left", padx=4)
        ttk.Button(buttons, text="Limpar lista...", command=self._on_clear_books).pack(side="left", padx=4)
        ttk.Button(buttons, text="Atualizar lista", command=self.refresh_books).pack(side="left", padx=4)

        buttons2 = ttk.Frame(parent)
        buttons2.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(buttons2, text="Importar vendas (CSV)...", command=self._on_import_sales).pack(side="left", padx=4)
        ttk.Button(
            buttons2, text="Atalho: catalogo IDE (opcional)...",
            command=lambda: self.notebook.select(self._catalog_tab),
        ).pack(side="left", padx=4)

    def refresh_books(self) -> None:
        for row in self.books_tree.get_children():
            self.books_tree.delete(row)

        session = get_session()
        products = session.query(Product).order_by(Product.active.desc(), Product.title).all()
        for product in self.books_pager.paginate(products):
            price = f"{product.price_id_livraria:.2f}" if product.price_id_livraria is not None else ""
            self.books_tree.insert(
                "", "end", iid=str(product.id),
                values=(
                    product.title, getattr(product, "author", None) or "", product.isbn or "", price,
                    product.origin.value, "sim" if product.active else "nao",
                ),
            )

    def _selected_book(self) -> Product | None:
        selection = self.books_tree.selection()
        if not selection:
            messagebox.showinfo("Selecione um livro", "Clique em um livro na lista primeiro.")
            return None
        product_id = int(selection[0])
        session = get_session()
        return session.get(Product, product_id)

    def _on_new_book(self) -> None:
        values = ask_book_fields(self)
        if not values:
            return
        session = get_session()
        existing = None
        if values.get("isbn"):
            existing = session.query(Product).filter(Product.isbn == values["isbn"]).first()
        if existing is None:
            existing = session.query(Product).filter(Product.title.ilike(values["title"])).one_or_none()
        if existing is not None:
            messagebox.showwarning(
                "Livro ja existe",
                f"'{existing.title}' ja esta na lista"
                + (f" (ISBN {existing.isbn})" if existing.isbn else "")
                + ". Use Editar.",
            )
            return

        product = Product(
            title=values["title"],
            author=values.get("author"),
            isbn=values["isbn"],
            sku=values["sku"],
            price_id_livraria=values["price"],
            origin=ProductOrigin.MANUAL,
            active=True,
        )
        session.add(product)
        session.commit()
        self.refresh_books()
        self._append_log(f"Livro adicionado: {values['title']}" + (f" | ISBN {values['isbn']}" if values.get("isbn") else ""))

    def _on_edit_book(self) -> None:
        product = self._selected_book()
        if product is None:
            return
        values = ask_book_fields(self, initial={
            "title": product.title,
            "author": getattr(product, "author", None),
            "isbn": product.isbn,
            "sku": product.sku,
            "price_id_livraria": product.price_id_livraria,
        })
        if not values:
            return
        session = get_session()
        db_product = session.get(Product, product.id)
        db_product.title = values["title"]
        db_product.author = values.get("author")
        db_product.isbn = values["isbn"]
        db_product.sku = values["sku"]
        db_product.price_id_livraria = values["price"]
        session.commit()
        self.refresh_books()
        self._append_log(f"Livro atualizado: {values['title']}")

    def _on_clear_books(self) -> None:
        session = get_session()
        total = session.query(Product).count()
        if total == 0:
            messagebox.showinfo("Lista vazia", "Nao ha livros na lista de monitoramento.")
            return
        if not messagebox.askyesno(
            "Limpar lista",
            f"Apagar os {total} livro(s) da lista de monitoramento e o historico de precos deles?\n\n"
            "Depois voce cadastra so os titulos que quiser acompanhar (da IDE ou de outras editoras).",
            parent=self,
        ):
            return
        removed = clear_monitored_books(delete_history=True)
        self.refresh_books()
        self.refresh_results()
        if hasattr(self, "_catalog_items") and self._catalog_items:
            self._after_catalog_change()
        self._append_log(f"Lista limpa: {removed} livro(s) removido(s).")

    def _on_toggle_book_active(self) -> None:
        product = self._selected_book()
        if product is None:
            return
        session = get_session()
        db_product = session.get(Product, product.id)
        db_product.active = not db_product.active
        session.commit()
        self.refresh_books()
        state = "ativado" if db_product.active else "desativado"
        self._append_log(f"Livro '{db_product.title}' {state}.")

    def _on_import_sales(self) -> None:
        options = ask_import_sales_options(self)
        if not options:
            return

        def task() -> list[Product]:
            return import_sales_csv(Path(options["csv_path"]), top_n=options["top_n"], mode=options["mode"])

        def on_success(selected: list[Product]) -> None:
            self.refresh_books()
            self._append_log(f"Import concluido: {len(selected)} titulo(s) selecionado(s).")

        self._run_background("Importando vendas", task, on_success)

    # ----------------------------------------------------------- aba resultados
    RESULT_STATUS_FILTERS = ("Todas", "ok", "incerto", "nao encontrado", "erro na coleta", "sem coleta")

    def _build_results_tab(self, parent: ttk.Frame) -> None:
        self._result_rows: dict[str, ResultRow] = {}

        self.results_info_var = tk.StringVar(value="")
        ttk.Label(parent, textvariable=self.results_info_var).pack(fill="x", padx=8, pady=(8, 0))

        filters = ttk.Frame(parent)
        filters.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(filters, text="Buscar livro:").pack(side="left")
        self.results_filter_var = tk.StringVar()
        self.results_filter_var.trace_add("write", lambda *_: self._results_filter_changed())
        ttk.Entry(filters, textvariable=self.results_filter_var, width=26).pack(side="left", padx=(4, 12))
        ttk.Label(filters, text="Concorrente:").pack(side="left")
        self.results_comp_var = tk.StringVar(value="Todos")
        self.results_comp_box = ttk.Combobox(
            filters, textvariable=self.results_comp_var, values=["Todos"], state="readonly", width=22
        )
        self.results_comp_box.pack(side="left", padx=(4, 12))
        self.results_comp_box.bind("<<ComboboxSelected>>", lambda _e: self._results_filter_changed())
        ttk.Label(filters, text="Situacao:").pack(side="left")
        self.results_status_var = tk.StringVar(value="Todas")
        status_box = ttk.Combobox(
            filters, textvariable=self.results_status_var, values=list(self.RESULT_STATUS_FILTERS),
            state="readonly", width=16,
        )
        status_box.pack(side="left", padx=4)
        status_box.bind("<<ComboboxSelected>>", lambda _e: self._results_filter_changed())

        columns = ("title", "competitor", "ide", "price", "diff", "status", "availability", "when")
        headers = {
            "title": "Livro", "competitor": "Concorrente", "ide": "Seu preco",
            "price": "Preco concorrente", "diff": "Diferenca", "status": "Situacao",
            "availability": "Disponibilidade", "when": "Coletado em",
        }
        widths = {"title": 190, "competitor": 120, "ide": 75, "price": 125, "diff": 165,
                  "status": 95, "availability": 90, "when": 90}
        self.results_tree = ttk.Treeview(parent, columns=columns, show="headings", height=12)
        for col in columns:
            self.results_tree.heading(col, text=headers[col])
            # So o titulo estica; as demais colunas mantem a largura para nada ficar cortado.
            self.results_tree.column(col, width=widths[col], minwidth=60, anchor="w", stretch=(col == "title"))
        self.results_tree.tag_configure("ide_cara", background="#FFC7CE")
        self.results_tree.tag_configure("ide_barata", background="#C6EFCE")
        self.results_tree.tag_configure("igual", background="#FFEB9C")
        self.results_tree.tag_configure("sem_preco", background="#E7E6E6")
        self.results_tree.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.results_pager = PagerBar(parent, on_change=self._render_results)
        self.results_pager.pack(fill="x", padx=8, pady=(0, 4))
        self.results_tree.bind("<<TreeviewSelect>>", lambda _e: self._show_result_detail())
        self.results_tree.bind("<Double-1>", lambda _e: self._on_open_result_url())

        self.results_detail_var = tk.StringVar(value="Clique em uma linha para ver os detalhes.")
        ttk.Label(parent, textvariable=self.results_detail_var, wraplength=940, foreground="#333333").pack(
            fill="x", padx=12, pady=(0, 4)
        )

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Button(buttons, text="Atualizar", command=self.refresh_results).pack(side="left", padx=4)
        ttk.Button(buttons, text="Abrir pagina do produto", command=self._on_open_result_url).pack(side="left", padx=4)
        ttk.Button(buttons, text="Gerar relatorio Excel", command=self._on_report).pack(side="left", padx=4)
        ttk.Label(
            parent,
            text="Cores: vermelho = seu preco mais caro, verde = seu preco mais barato, amarelo = igual, cinza = sem preco.",
            foreground="#555555",
        ).pack(fill="x", padx=12, pady=(0, 8))

        self.refresh_results()

    def refresh_results(self) -> None:
        rows = latest_results()
        self._all_results = rows
        names = sorted({r.competitor_name for r in rows})
        self.results_comp_box.configure(values=["Todos", *names])
        if self.results_comp_var.get() not in ("Todos", *names):
            self.results_comp_var.set("Todos")
        self.results_info_var.set(summarize_results(rows))
        self._render_results()

    def _results_filter_changed(self) -> None:
        if hasattr(self, "results_pager"):
            self.results_pager.reset()
        self._render_results()

    def _render_results(self) -> None:
        if not hasattr(self, "results_tree") or not hasattr(self, "_all_results"):
            return
        self.results_tree.delete(*self.results_tree.get_children())
        self._result_rows = {}
        needle = self.results_filter_var.get().strip().lower()
        comp = self.results_comp_var.get()
        status = self.results_status_var.get()
        filtered: list[tuple[int, ResultRow]] = []
        for index, row in enumerate(self._all_results):
            if needle and needle not in f"{row.title} {row.isbn}".lower():
                continue
            if comp != "Todos" and row.competitor_name != comp:
                continue
            if status != "Todas" and row.status_label != status:
                continue
            filtered.append((index, row))

        for index, row in self.results_pager.paginate(filtered):
            iid = str(index)
            self._result_rows[iid] = row
            ide = f"R$ {row.ide_price:.2f}" if row.ide_price is not None else "-"
            if row.price is not None:
                price = f"R$ {row.price:.2f}"
                if row.price_regular is not None:
                    price += f" (de {row.price_regular:.2f})"
            else:
                price = "-"
            when = row.collected_at.strftime("%d/%m %H:%M") if row.collected_at else "-"
            tag = row.verdict or "sem_preco"
            self.results_tree.insert(
                "", "end", iid=iid, tags=(tag,),
                values=(row.title, row.competitor_name, ide, price, row.diff_text or "-",
                        row.status_label, row.availability or "-", when),
            )

    def _selected_result(self) -> ResultRow | None:
        selection = self.results_tree.selection()
        return self._result_rows.get(selection[0]) if selection else None

    def _show_result_detail(self) -> None:
        row = self._selected_result()
        if row is None:
            return
        parts = [f"{row.title} em {row.competitor_name}: {row.status_label}"]
        if row.confidence is not None:
            parts.append(f"confianca do match {row.confidence:.0%}")
        if row.error:
            parts.append(f"erro: {row.error}")
        if row.url:
            parts.append(f"link: {row.url}")
        self.results_detail_var.set(" | ".join(parts))

    def _on_open_result_url(self) -> None:
        row = self._selected_result()
        if row is None:
            messagebox.showinfo("Selecione uma linha", "Clique em uma linha da lista primeiro.")
            return
        if not row.url:
            messagebox.showinfo("Sem link", "Essa linha nao tem pagina de produto (nao foi encontrado).")
            return
        webbrowser.open(row.url)

    # ------------------------------------------------------- aba catalogo IDE
    def _build_catalog_tab(self, parent: ttk.Frame) -> None:
        self._catalog_items: dict[str, CatalogItem] = {}
        self._catalog_flags: dict[str, bool] = {}
        self._catalog_thread: threading.Thread | None = None
        self._catalog_result: dict[str, Any] = {}

        top = ttk.Frame(parent)
        top.pack(fill="x", padx=8, pady=(8, 0))
        self.catalog_info_var = tk.StringVar(value="Atalho opcional do catalogo IDE: carregando...")
        ttk.Label(top, textvariable=self.catalog_info_var).pack(side="left")

        filters = ttk.Frame(parent)
        filters.pack(fill="x", padx=8, pady=(6, 0))
        ttk.Label(filters, text="Buscar:").pack(side="left")
        self.catalog_filter_var = tk.StringVar()
        self.catalog_filter_var.trace_add("write", lambda *_: self._catalog_filter_changed())
        ttk.Entry(filters, textvariable=self.catalog_filter_var, width=34).pack(side="left", padx=(4, 12))
        self.catalog_hide_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            filters, text="Ocultar os ja monitorados", variable=self.catalog_hide_var,
            command=self._catalog_filter_changed,
        ).pack(side="left")

        columns = ("title", "author", "isbn", "price", "monitored")
        headers = {
            "title": "Titulo", "author": "Autor", "isbn": "ISBN",
            "price": "Preco (c/ desconto)", "monitored": "Monitorado",
        }
        widths = {"title": 280, "author": 220, "isbn": 120, "price": 120, "monitored": 90}
        self.catalog_tree = ttk.Treeview(parent, columns=columns, show="headings", height=12, selectmode="extended")
        for col in columns:
            self.catalog_tree.heading(col, text=headers[col])
            self.catalog_tree.column(col, width=widths[col], anchor="w")
        self.catalog_tree.pack(fill="both", expand=True, padx=8, pady=(8, 4))
        self.catalog_pager = PagerBar(parent, on_change=self._render_catalog)
        self.catalog_pager.pack(fill="x", padx=8, pady=(0, 4))
        self.catalog_tree.bind("<Double-1>", lambda _evt: self._on_catalog_add_selected())
        self.catalog_tree.bind("<Control-a>", self._catalog_select_all)

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Button(
            buttons, text="Adicionar selecionados ao monitoramento", command=self._on_catalog_add_selected
        ).pack(side="left", padx=4)
        ttk.Button(buttons, text="Adicionar todos (de todas as paginas)", command=self._on_catalog_add_all).pack(side="left", padx=4)
        ttk.Button(buttons, text="Parar de monitorar selecionados", command=self._on_catalog_remove_selected).pack(side="left", padx=4)
        ttk.Button(buttons, text="Atualizar catalogo", command=lambda: self._start_catalog_refresh(manual=True)).pack(side="left", padx=4)
        ttk.Label(
            parent,
            text="Opcional: use esta aba so se quiser puxar um titulo do catalogo IDE. O fluxo principal e cadastrar na aba Livros (ISBN/titulo/autor/seu preco). Precos do catalogo IDE sao de e-book.",
            foreground="#555555",
        ).pack(fill="x", padx=12, pady=(0, 8))

        items, fetched_at = load_cache()
        self._set_catalog_items(items, fetched_at)

    def _set_catalog_items(self, items: list[CatalogItem], fetched_at: str | None) -> None:
        accepted, _skipped = select_items(items)
        self._catalog_items = {item.doc_id: item for item in accepted}
        self._catalog_flags = monitored_flags(accepted) if accepted else {}
        if accepted:
            when = ""
            if fetched_at:
                try:
                    when = f" (atualizado em {datetime.fromisoformat(fetched_at).strftime('%d/%m/%Y %H:%M')})"
                except ValueError:
                    pass
            self.catalog_info_var.set(f"Catalogo da IDE: {len(accepted)} livros{when}")
        else:
            self.catalog_info_var.set("Catalogo da IDE: ainda nao baixado. Clique em 'Atualizar catalogo'.")
        self._render_catalog()

    def _catalog_filter_changed(self) -> None:
        if hasattr(self, "catalog_pager"):
            self.catalog_pager.reset()
        self._render_catalog()

    def _visible_catalog_ids(self) -> list[str]:
        """doc_ids que passam nos filtros (busca e 'ocultar monitorados'), em ordem alfabetica."""
        needle = self.catalog_filter_var.get().strip().lower()
        ids: list[str] = []
        for doc_id, item in sorted(self._catalog_items.items(), key=lambda kv: kv[1].title.lower()):
            if self.catalog_hide_var.get() and self._catalog_flags.get(doc_id, False):
                continue
            if needle and needle not in f"{item.title} {item.author} {item.isbn or ''}".lower():
                continue
            ids.append(doc_id)
        return ids

    def _render_catalog(self) -> None:
        if not hasattr(self, "catalog_tree") or not hasattr(self, "catalog_pager"):
            return
        self.catalog_tree.delete(*self.catalog_tree.get_children())
        for doc_id in self.catalog_pager.paginate(self._visible_catalog_ids()):
            item = self._catalog_items[doc_id]
            monitored = self._catalog_flags.get(doc_id, False)
            self.catalog_tree.insert(
                "", "end", iid=doc_id,
                values=(item.title, item.author, item.isbn or "", f"R$ {item.final_price:.2f}",
                        "sim" if monitored else ""),
            )

    def _catalog_select_all(self, _event=None) -> str:
        self.catalog_tree.selection_set(self.catalog_tree.get_children())
        return "break"

    def _selected_catalog_items(self, everything: bool = False) -> list[CatalogItem]:
        # "todos" = tudo que passa nos filtros, em todas as paginas (nao so a pagina na tela).
        ids = self._visible_catalog_ids() if everything else self.catalog_tree.selection()
        return [self._catalog_items[i] for i in ids if i in self._catalog_items]

    def _on_catalog_add_selected(self) -> None:
        items = self._selected_catalog_items()
        if not items:
            messagebox.showinfo("Selecione livros", "Clique em um ou mais livros da lista primeiro.")
            return
        self._add_catalog_items(items)

    def _on_catalog_add_all(self) -> None:
        items = self._selected_catalog_items(everything=True)
        if not items:
            messagebox.showinfo("Lista vazia", "Nao ha livros listados para adicionar.")
            return
        if not messagebox.askyesno(
            "Adicionar todos",
            f"Adicionar os {len(items)} livros listados ao monitoramento?\n\n"
            "Lembre: quanto mais livros, mais demorada e a coleta.",
            parent=self,
        ):
            return
        self._add_catalog_items(items)

    def _add_catalog_items(self, items: list[CatalogItem]) -> None:
        report = add_items(items)
        self._after_catalog_change()
        self._append_log(
            f"Catalogo IDE: {len(report.added)} livro(s) adicionado(s) ao monitoramento, "
            f"{len(report.updated)} ja estava(m) na lista (preco atualizado)."
        )

    def _on_catalog_remove_selected(self) -> None:
        items = self._selected_catalog_items()
        if not items:
            messagebox.showinfo("Selecione livros", "Clique em um ou mais livros da lista primeiro.")
            return
        changed = stop_monitoring(items)
        self._after_catalog_change()
        self._append_log(f"Catalogo IDE: {changed} livro(s) deixaram de ser monitorados (historico mantido).")

    def _after_catalog_change(self) -> None:
        self._catalog_flags = monitored_flags(list(self._catalog_items.values()))
        self._render_catalog()
        self.refresh_books()

    def _start_catalog_refresh(self, manual: bool = False) -> None:
        """Baixa o catalogo em segundo plano (sem travar a janela e sem pop-up de erro)."""
        if self._catalog_thread is not None:
            return
        self.catalog_info_var.set("Catalogo da IDE: atualizando...")
        self._catalog_result = {}

        def worker() -> None:
            try:
                self._catalog_result["items"], self._catalog_result["report"] = refresh_catalog()
            except Exception as exc:  # noqa: BLE001 - erro de rede nao deve derrubar a interface
                self._catalog_result["error"] = exc

        self._catalog_thread = threading.Thread(target=worker, daemon=True)
        self._catalog_thread.start()
        self.after(300, lambda: self._poll_catalog_refresh(manual))

    def _poll_catalog_refresh(self, manual: bool) -> None:
        if self._catalog_thread is not None and self._catalog_thread.is_alive():
            self.after(300, lambda: self._poll_catalog_refresh(manual))
            return
        self._catalog_thread = None
        result = self._catalog_result
        if "error" in result:
            items, fetched_at = load_cache()
            self._set_catalog_items(items, fetched_at)
            self._append_log(f"Catalogo IDE: nao consegui atualizar ({result['error']}). Usando a copia salva.")
            if manual:
                messagebox.showwarning("Catalogo IDE", str(result["error"]))
            return
        _items, fetched_at = load_cache()
        self._set_catalog_items(result["items"], fetched_at)
        report = result.get("report")
        self.refresh_books()
        if report is not None and (report.updated or manual):
            self._append_log(f"Catalogo IDE atualizado: {len(report.updated)} preco(s) de livros monitorados conferido(s).")

    # ------------------------------------------------------ aba concorrentes
    def _build_competitors_tab(self, parent: ttk.Frame) -> None:
        columns = ("name", "base_url", "active", "status")
        headers = {"name": "Nome", "base_url": "URL base", "active": "Ativo", "status": "Status da busca"}
        widths = {"name": 170, "base_url": 280, "active": 60, "status": 220}

        self.competitors_tree = ttk.Treeview(parent, columns=columns, show="headings", height=10)
        for col in columns:
            self.competitors_tree.heading(col, text=headers[col])
            self.competitors_tree.column(col, width=widths[col], anchor="w")
        self.competitors_tree.pack(fill="both", expand=True, padx=8, pady=8)
        self.competitors_tree.bind("<Double-1>", lambda _evt: self._on_edit_competitor())

        buttons = ttk.Frame(parent)
        buttons.pack(fill="x", padx=8, pady=(0, 4))
        ttk.Button(buttons, text="Mapear com IA", command=self._on_map_selected).pack(side="left", padx=4)
        ttk.Button(buttons, text="Mapear todos pendentes", command=self._on_map_pending).pack(side="left", padx=4)
        ttk.Button(buttons, text="Desfazer ultimo mapeamento", command=self._on_undo_mapping).pack(side="left", padx=4)

        buttons2 = ttk.Frame(parent)
        buttons2.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(buttons2, text="Novo concorrente...", command=self._on_new_competitor).pack(side="left", padx=4)
        ttk.Button(buttons2, text="Editar seletores/URL...", command=self._on_edit_competitor).pack(side="left", padx=4)
        ttk.Button(buttons2, text="Atualizar lista", command=self.refresh_competitors).pack(side="left", padx=4)

        note = (
            "A IA descobre como buscar em cada site (uma unica vez) e a receita fica salva: a coleta\n"
            "normal nao gasta nada. Se um site mudar de layout, a IA ajusta sozinha (aba 'Configuracoes\n"
            "de IA'). 'Editar seletores/URL' continua disponivel para ajustes manuais."
        )
        ttk.Label(parent, text=note, foreground="#555555").pack(anchor="w", padx=8, pady=(0, 8))

    def refresh_competitors(self) -> None:
        for row in self.competitors_tree.get_children():
            self.competitors_tree.delete(row)
        for competitor in load_competitors(active_only=False):
            self.competitors_tree.insert(
                "", "end", iid=competitor.key,
                values=(
                    competitor.name, competitor.base_url, "sim" if competitor.active else "nao",
                    competitor_status_text(competitor),
                ),
            )

    # ------------------------------------------------------- mapeamento por IA
    def _ai_ready_or_warn(self) -> bool:
        if has_api_key():
            return True
        messagebox.showinfo(
            "Chave da API necessaria",
            "Para a IA mapear os sites, cole a chave da API do Claude na aba 'Configuracoes de IA'.",
        )
        self.notebook.select(self._ai_tab)
        return False

    def _run_mapping(self, keys: list[str]) -> None:
        from id_monitor.ai.mapper import map_competitors

        names = {c.key: c.name for c in load_competitors(active_only=False)}

        def task() -> list:
            return map_competitors(keys)

        def on_success(results: list) -> None:
            self.refresh_competitors()
            self._append_log("\nResultado do mapeamento:")
            for key, result in results:
                icon = "OK" if result.success else ("FORA DO AR" if result.state == "down" else "FALHOU")
                self._append_log(f"  - {names.get(key, key)}: {icon} - {result.message}")
            if any(not result.success for _key, result in results):
                self._append_log(
                    "Os sites que falharam continuam como estavam. Voce pode tentar de novo, ou usar "
                    "'Editar seletores/URL...' para ajustar na mao."
                )

        label = "Mapeando com IA: " + ", ".join(names.get(k, k) for k in keys)
        self._run_background(label, task, on_success)

    def _on_map_selected(self) -> None:
        key = self._selected_competitor_key()
        if not key or not self._ai_ready_or_warn():
            return
        self._run_mapping([key])

    def _pending_competitor_keys(self) -> list[str]:
        return [
            c.key for c in load_competitors(active_only=True)
            if not c.has_recipe and c.status != "fora_do_ar"
        ]

    def _on_map_pending(self) -> None:
        keys = self._pending_competitor_keys()
        if not keys:
            messagebox.showinfo("Nada pendente", "Todos os concorrentes ativos ja tem receita de busca.")
            return
        if self._ai_ready_or_warn():
            self._run_mapping(keys)

    def _on_undo_mapping(self) -> None:
        key = self._selected_competitor_key()
        if not key:
            return
        try:
            restore_last_recipe_backup(key)
        except FileNotFoundError:
            messagebox.showinfo("Nada para desfazer", "Nao ha mapeamento anterior guardado para este concorrente.")
            return
        self.refresh_competitors()
        self._append_log(f"Mapeamento anterior de '{get_competitor(key).name}' restaurado.")

    def _on_new_competitor(self) -> None:
        values = ask_new_competitor(self, can_map=has_api_key())
        if not values:
            return
        try:
            competitor = add_competitor(values["name"], values["base_url"])
        except ValueError as exc:
            messagebox.showwarning("Dados invalidos", str(exc))
            return
        sync_competitors_from_config(get_session())
        self.refresh_competitors()
        self._append_log(f"Concorrente cadastrado: {competitor.name}.")
        if values["map_now"]:
            self._run_mapping([competitor.key])

    def _notify_pending_mapping(self) -> None:
        """Ao abrir o programa, avisa se ha concorrentes ativos sem receita de busca."""
        keys = self._pending_competitor_keys()
        if not keys:
            return
        names = {c.key: c.name for c in load_competitors(active_only=True)}
        listing = "\n".join(f"  - {names[k]}" for k in keys)
        if has_api_key():
            if messagebox.askyesno(
                "Sites para mapear",
                f"Estes concorrentes ainda nao tem a busca mapeada:\n{listing}\n\n"
                "Mapear todos agora com a IA? (leva alguns minutos e gasta poucos centavos)",
            ):
                self._run_mapping(keys)
        else:
            messagebox.showinfo(
                "Sites para mapear",
                f"Estes concorrentes ainda nao tem a busca mapeada:\n{listing}\n\n"
                "Cole a chave da API do Claude na aba 'Configuracoes de IA' e depois use "
                "'Mapear todos pendentes' na aba 'Concorrentes'.",
            )
            self.notebook.select(self._ai_tab)

    # ------------------------------------------------------- aba configuracoes de IA
    def _build_ai_tab(self, parent: ttk.Frame) -> None:
        intro = (
            "A IA (Claude) so e usada para descobrir como buscar em um site novo e para consertar a\n"
            "busca quando um site muda de layout. A coleta de precos do dia a dia NAO gasta nada."
        )
        ttk.Label(parent, text=intro, foreground="#555555").grid(row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(12, 10))

        ttk.Label(parent, text="Chave da API do Claude:").grid(row=1, column=0, sticky="w", padx=12, pady=4)
        self.api_key_var = tk.StringVar()
        ttk.Entry(parent, textvariable=self.api_key_var, show="*", width=52).grid(row=1, column=1, sticky="w", pady=4)
        self.api_key_status_var = tk.StringVar()
        ttk.Label(parent, textvariable=self.api_key_status_var, foreground="#555555").grid(
            row=2, column=1, sticky="w", pady=(0, 4)
        )

        key_buttons = ttk.Frame(parent)
        key_buttons.grid(row=3, column=1, sticky="w", pady=(0, 10))
        ttk.Button(key_buttons, text="Salvar chave", command=self._on_save_api_key).pack(side="left", padx=(0, 6))
        ttk.Button(key_buttons, text="Testar chave", command=self._on_test_api_key).pack(side="left", padx=6)
        ttk.Button(key_buttons, text="Remover chave", command=self._on_delete_api_key).pack(side="left", padx=6)

        ttk.Label(parent, text="Modelo:").grid(row=4, column=0, sticky="w", padx=12, pady=4)
        settings = load_settings()
        self.model_var = tk.StringVar(value=settings.model)
        ttk.Combobox(parent, textvariable=self.model_var, values=SUGGESTED_MODELS, width=30).grid(
            row=4, column=1, sticky="w", pady=4
        )

        self.auto_repair_var = tk.BooleanVar(value=settings.auto_repair)
        ttk.Checkbutton(
            parent,
            text="Reparar automaticamente quando a coleta falhar (precisa de chave)",
            variable=self.auto_repair_var,
        ).grid(row=5, column=1, sticky="w", pady=(8, 4))

        ttk.Button(parent, text="Salvar opcoes", command=self._on_save_ai_options).grid(
            row=6, column=1, sticky="w", pady=(6, 10)
        )

        privacy = (
            "A chave fica guardada no Cofre de Credenciais do Windows (nunca em arquivo do projeto).\n"
            "Obtenha uma chave em console.anthropic.com. Para enviar ao Claude, so vai o HTML\n"
            "simplificado da pagina de resultados de busca dos sites concorrentes."
        )
        ttk.Label(parent, text=privacy, foreground="#555555").grid(row=7, column=0, columnspan=3, sticky="w", padx=12)

        self._refresh_api_key_status()

    def _refresh_api_key_status(self) -> None:
        key = get_api_key()
        self.api_key_status_var.set(f"Chave salva: {mask_key(key)}" if key else "Nenhuma chave configurada.")

    def _on_save_api_key(self) -> None:
        typed = self.api_key_var.get().strip()
        if not typed:
            messagebox.showwarning("Chave em branco", "Cole a chave da API no campo antes de salvar.")
            return
        try:
            set_api_key(typed)
        except AIConfigError as exc:
            messagebox.showerror("Erro ao salvar", str(exc))
            return
        self.api_key_var.set("")
        self._refresh_api_key_status()
        self._append_log("Chave da API salva no cofre de credenciais.")

    def _on_delete_api_key(self) -> None:
        if not messagebox.askyesno("Remover chave", "Remover a chave da API do cofre de credenciais?"):
            return
        try:
            delete_api_key()
        except AIConfigError as exc:
            messagebox.showerror("Erro", str(exc))
            return
        self._refresh_api_key_status()
        self._append_log("Chave da API removida.")

    def _on_test_api_key(self) -> None:
        typed = self.api_key_var.get().strip() or None
        model = self.model_var.get().strip() or None
        if typed is None and not has_api_key():
            messagebox.showinfo("Sem chave", "Cole a chave no campo (ou salve uma) antes de testar.")
            return

        def task() -> str:
            from id_monitor.ai.client import ClaudeClient

            return ClaudeClient(api_key=typed, model=model).test_connection()

        def on_success(used_model: str) -> None:
            self._append_log(f"Chave OK. Modelo usado no teste: {used_model}.")
            messagebox.showinfo("Chave valida", f"Tudo certo: a chave funcionou com o modelo {used_model}.")

        self._run_background("Testando a chave da API", task, on_success)

    def _on_save_ai_options(self) -> None:
        model = self.model_var.get().strip()
        if not model:
            messagebox.showwarning("Modelo em branco", "Escolha ou digite um modelo.")
            return
        save_settings(AISettings(model=model, auto_repair=bool(self.auto_repair_var.get())))
        self._append_log("Opcoes de IA salvas.")

    def _selected_competitor_key(self) -> str | None:
        selection = self.competitors_tree.selection()
        if not selection:
            messagebox.showinfo("Selecione um concorrente", "Clique em um concorrente na lista primeiro.")
            return None
        return selection[0]

    def _on_edit_competitor(self) -> None:
        key = self._selected_competitor_key()
        if not key:
            return
        competitor = get_competitor(key)
        updates = ask_competitor_fields(self, competitor)
        if not updates:
            return
        update_competitor(key, updates)
        self.refresh_competitors()
        self._append_log(f"Concorrente '{competitor.name}' atualizado.")

    # --------------------------------------------------------------- acoes
    def _on_collect_all(self) -> None:
        self._run_background("Coletando precos em todos os concorrentes", collect_prices, on_success=self._after_collect)

    def _on_collect_one(self) -> None:
        session = get_session()
        titles = [p.title for p in session.query(Product).filter(Product.active.is_(True)).order_by(Product.title).all()]
        if not titles:
            messagebox.showinfo("Nenhum livro", "Nao ha livros ativos para coletar. Cadastre um livro primeiro.")
            return
        chosen = ask_single_book_title(self, titles)
        if not chosen:
            return
        self._run_background(
            f"Coleta avulsa: {chosen}",
            lambda: collect_prices(product_titles=[chosen]),
            on_success=self._after_collect,
        )

    def _after_collect(self, records_written: int) -> None:
        self._append_log(f"Coleta concluida: {records_written} registro(s) gravados.")
        self.refresh_results()
        self.notebook.select(self._results_tab)  # mostra o resultado na hora, antes de exportar

    def _on_tab_changed(self, _event=None) -> None:
        if self.notebook.select() == str(self._results_tab):
            self.refresh_results()

    def _on_report(self) -> None:
        def on_success(path: Path) -> None:
            self._append_log(f"Relatorio gerado: {path}")
            if messagebox.askyesno("Relatorio gerado", f"Relatorio gerado em:\n{path}\n\nAbrir agora?"):
                self._open_path(path)

        self._run_background("Gerando relatorio", build_report, on_success)

    def _on_open_reports(self) -> None:
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        self._open_path(REPORTS_DIR)

    def _on_smoke_test(self) -> None:
        def on_success(results) -> None:
            self._append_log("\nResultado do smoke-test:")
            any_issue = False
            for result in results:
                self._append_log(f"  - {result.competitor_name}: {result.status_label}")
                if not result.ok:
                    any_issue = True
            if any_issue:
                self._append_log(
                    "Atencao: pelo menos um concorrente parece com problema. "
                    "Ajuste os seletores na aba 'Concorrentes'."
                )

        self._run_background("Rodando smoke-test", run_smoke_test, on_success)

    def _startup_update_check(self) -> None:
        """Aviso discreto na abertura se houver versao nova (nao bloqueia nem baixa sozinho)."""
        from id_monitor.updater import UpdateError, check_for_update, load_update_settings, updates_configured

        if not updates_configured():
            return
        try:
            settings = load_update_settings()
            if not settings.check_on_startup:
                return
            update = check_for_update(settings)
        except UpdateError:
            return
        except Exception:  # noqa: BLE001 - startup nao deve atrapalhar
            return
        if update is None:
            return
        self.status_var.set(f"Nova versao disponivel: {update.new_version}")
        self._append_log(
            f"Atualizacao disponivel: {update.current_version} -> {update.new_version}. "
            "Use o botao 'Verificar atualizacoes'."
        )

    def _on_check_update(self) -> None:
        from id_monitor.updater import UpdateError, apply_update, check_for_update, current_version, updates_configured

        if not updates_configured():
            messagebox.showinfo(
                "Atualizacao",
                "Atualizacao automatica ainda nao esta configurada.\n\n"
                "Edite config/update.yaml e coloque o repositorio publico do GitHub "
                "(ex.: minha-org/monitor-precos), depois publique Releases com o .zip.",
            )
            return

        def work():
            return check_for_update()

        def on_success(update) -> None:
            if update is None:
                messagebox.showinfo(
                    "Atualizacao",
                    f"Ja esta na versao mais recente ({current_version()}).",
                )
                return
            notes = (update.release_notes or "").strip()
            if len(notes) > 400:
                notes = notes[:400] + "..."
            detail = (
                f"Versao instalada: {update.current_version}\n"
                f"Nova versao: {update.new_version}\n"
                f"Pacote: {update.asset_name}\n"
            )
            if notes:
                detail += f"\nNotas:\n{notes}\n"
            detail += "\nBaixar e aplicar agora?\n(Seus livros, data/ e config/update.yaml sao preservados.)"
            if not messagebox.askyesno("Atualizacao disponivel", detail):
                return

            def apply_work():
                return apply_update(update, progress=lambda m: logging.getLogger("id_monitor").info(m))

            def after_apply(_result) -> None:
                messagebox.showinfo(
                    "Atualizacao aplicada",
                    f"Versao {update.new_version} instalada.\nFeche e abra o programa de novo.",
                )

            self._run_background("Aplicando atualizacao", apply_work, after_apply)

        self._run_background("Verificando atualizacoes", work, on_success)

    @staticmethod
    def _open_path(path: Path) -> None:
        if sys.platform.startswith("win"):
            os.startfile(str(path))  # noqa: S606 - abrir no Explorer/visualizador padrao e intencional
        else:
            subprocess.run(["xdg-open", str(path)], check=False)

    # ------------------------------------------------------------ segundo plano
    def _set_buttons_enabled(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in self._action_buttons:
            button.configure(state=state)

    def _run_background(self, label: str, func: Callable[[], Any], on_success: Callable[[Any], None] | None = None) -> None:
        if self._current_thread is not None:
            messagebox.showinfo("Aguarde", "Ja tem uma tarefa em andamento. Aguarde terminar.")
            return

        self._set_buttons_enabled(False)
        self.status_var.set(f"{label}... aguarde.")
        self._append_log(f"\n----- {label} -----")

        handler = _QueueLogHandler(self._log_queue)
        handler.setFormatter(logging.Formatter("%(message)s"))
        id_monitor_logger = logging.getLogger("id_monitor")
        id_monitor_logger.addHandler(handler)
        id_monitor_logger.setLevel(logging.INFO)

        self._task_result = {}
        self._task_on_success = on_success

        def worker() -> None:
            try:
                self._task_result["value"] = func()
            except Exception as exc:  # noqa: BLE001 - mostramos qualquer erro ao usuario
                self._task_result["error"] = exc
            finally:
                id_monitor_logger.removeHandler(handler)

        thread = threading.Thread(target=worker, daemon=True)
        self._current_thread = thread
        thread.start()

    def _finalize_task(self) -> None:
        self._current_thread = None
        self._set_buttons_enabled(True)

        if "error" in self._task_result:
            error = self._task_result["error"]
            self.status_var.set("Erro na ultima operacao.")
            self._append_log(f"ERRO: {error}")
            messagebox.showerror("Erro", str(error))
        else:
            self.status_var.set("Pronto.")
            if self._task_on_success is not None:
                self._task_on_success(self._task_result.get("value"))

        self._task_on_success = None

    def _poll_log_queue(self) -> None:
        try:
            while True:
                line = self._log_queue.get_nowait()
                self._append_log(line)
        except queue.Empty:
            pass

        if self._current_thread is not None and not self._current_thread.is_alive():
            self._finalize_task()

        self.after(150, self._poll_log_queue)

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", line + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")


def main() -> None:
    app = App()
    app.mainloop()


if __name__ == "__main__":
    main()
