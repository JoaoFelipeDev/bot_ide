"""Janelas (dialogs) modais usadas pela interface grafica: cadastrar/editar livro, editar os
seletores de um concorrente, e escolher o arquivo/opcoes do import de vendas.

Cada funcao abre uma janela, espera o usuario preencher e clicar em "Salvar"/"Cancelar", e
retorna um dict com os valores preenchidos (ou None se o usuario cancelou).
"""
from __future__ import annotations

import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any

from id_monitor.matching.isbn import normalize_isbn_digits


def _center_on_parent(window: tk.Toplevel, parent: tk.Tk) -> None:
    window.update_idletasks()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    ww, wh = window.winfo_width(), window.winfo_height()
    window.geometry(f"+{px + (pw - ww) // 2}+{py + (ph - wh) // 3}")


def ask_book_fields(parent: tk.Tk, initial: dict[str, Any] | None = None) -> dict[str, Any] | None:
    """Abre um formulario para cadastrar ou editar um livro.

    Retorna {'title', 'author', 'isbn', 'sku', 'price'} ou None se cancelado.
    ISBN e o campo mais importante para achar a edicao certa nos concorrentes.
    """
    initial = initial or {}
    result: dict[str, Any] = {}

    win = tk.Toplevel(parent)
    win.title("Livro para monitorar" if not initial else f"Editar: {initial.get('title', '')}")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()

    frame = ttk.Frame(win, padding=12)
    frame.grid()

    ttk.Label(
        frame,
        text="Preencha o livro que voce quer acompanhar nos sites concorrentes.\n"
             "Pode ser da IDE ou de outra editora. ISBN e o mais importante.",
        foreground="#555555",
        justify="left",
    ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

    ttk.Label(frame, text="ISBN:*").grid(row=1, column=0, sticky="w", pady=4)
    isbn_var = tk.StringVar(value=initial.get("isbn") or "")
    isbn_entry = ttk.Entry(frame, textvariable=isbn_var, width=46)
    isbn_entry.grid(row=1, column=1, pady=4)

    ttk.Label(frame, text="Titulo:*").grid(row=2, column=0, sticky="w", pady=4)
    title_var = tk.StringVar(value=initial.get("title", ""))
    title_entry = ttk.Entry(frame, textvariable=title_var, width=46)
    title_entry.grid(row=2, column=1, pady=4)

    ttk.Label(frame, text="Autor:").grid(row=3, column=0, sticky="w", pady=4)
    author_var = tk.StringVar(value=initial.get("author") or "")
    ttk.Entry(frame, textvariable=author_var, width=46).grid(row=3, column=1, pady=4)

    ttk.Label(frame, text="Seu preco (referencia):").grid(row=4, column=0, sticky="w", pady=4)
    price_val = initial.get("price_id_livraria")
    price_var = tk.StringVar(value="" if price_val is None else str(price_val))
    ttk.Entry(frame, textvariable=price_var, width=46).grid(row=4, column=1, pady=4)

    ttk.Label(frame, text="SKU (opcional):").grid(row=5, column=0, sticky="w", pady=4)
    sku_var = tk.StringVar(value=initial.get("sku") or "")
    ttk.Entry(frame, textvariable=sku_var, width=46).grid(row=5, column=1, pady=4)

    button_row = ttk.Frame(frame)
    button_row.grid(row=6, column=0, columnspan=2, pady=(12, 0))

    def on_save() -> None:
        title = title_var.get().strip()
        isbn_raw = isbn_var.get().strip()
        isbn = normalize_isbn_digits(isbn_raw) if isbn_raw else None
        if isbn_raw and not isbn:
            messagebox.showwarning(
                "ISBN invalido",
                "Informe um ISBN-10 ou ISBN-13 valido (so numeros, com ou sem hifen).",
                parent=win,
            )
            return
        if not title:
            messagebox.showwarning("Campo obrigatorio", "Informe o titulo do livro.", parent=win)
            return
        if not isbn:
            if not messagebox.askyesno(
                "Sem ISBN",
                "Sem ISBN a busca pode pegar outra edicao/editora com o mesmo titulo.\n\n"
                "Salvar mesmo assim?",
                parent=win,
            ):
                return

        price_text = price_var.get().strip().replace(",", ".")
        price = None
        if price_text:
            try:
                price = float(price_text)
            except ValueError:
                messagebox.showwarning("Preco invalido", "Use um numero, ex: 39.90", parent=win)
                return

        result["title"] = title
        result["author"] = author_var.get().strip() or None
        result["isbn"] = isbn
        result["sku"] = sku_var.get().strip() or None
        result["price"] = price
        win.destroy()

    def on_cancel() -> None:
        result.clear()
        win.destroy()

    ttk.Button(button_row, text="Salvar", command=on_save).grid(row=0, column=0, padx=4)
    ttk.Button(button_row, text="Cancelar", command=on_cancel).grid(row=0, column=1, padx=4)

    isbn_entry.focus_set()
    _center_on_parent(win, parent)
    win.wait_window()
    return result or None


def ask_competitor_fields(parent: tk.Tk, competitor: Any) -> dict[str, Any] | None:
    """Abre um formulario para editar o nome, URL base, status ativo/inativo e os seletores de
    busca (search_url_template + CSS selectors) de um concorrente. Retorna um dict com os
    campos (prontos para id_monitor.config.update_competitor) ou None se cancelado."""
    result: dict[str, Any] = {}

    win = tk.Toplevel(parent)
    win.title(f"Editar concorrente: {competitor.name}")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()

    frame = ttk.Frame(win, padding=12)
    frame.grid()

    fields: list[tuple[str, str, Any]] = [
        ("name", "Nome:", competitor.name),
        ("base_url", "URL base:", competitor.base_url),
        ("search_mode", "Modo de busca (url/type):", getattr(competitor, "search_mode", None) or "url"),
        ("search_url_template", "URL de busca ({query}):", competitor.search_url_template or ""),
        ("search_input_selector", "Seletor do campo de busca:", competitor.search_input_selector or ""),
        ("result_item_selector", "Seletor do item:", competitor.result_item_selector or ""),
        ("title_selector", "Seletor do titulo:", competitor.title_selector or ""),
        ("price_selector", "Seletor do preco:", competitor.price_selector or ""),
        ("link_selector", "Seletor do link:", competitor.link_selector or ""),
        ("availability_selector", "Seletor da disponibilidade:", competitor.availability_selector or ""),
    ]

    vars_by_key: dict[str, tk.StringVar] = {}
    for row_idx, (key, label, value) in enumerate(fields):
        ttk.Label(frame, text=label).grid(row=row_idx, column=0, sticky="w", pady=3)
        var = tk.StringVar(value=value)
        vars_by_key[key] = var
        ttk.Entry(frame, textvariable=var, width=58).grid(row=row_idx, column=1, pady=3)

    active_var = tk.BooleanVar(value=bool(competitor.active))
    ttk.Checkbutton(frame, text="Ativo", variable=active_var).grid(
        row=len(fields), column=1, sticky="w", pady=6
    )

    button_row = ttk.Frame(frame)
    button_row.grid(row=len(fields) + 1, column=0, columnspan=2, pady=(10, 0))

    def on_save() -> None:
        for key, var in vars_by_key.items():
            text = var.get().strip()
            result[key] = text or None
        if not result.get("name") or not result.get("base_url"):
            messagebox.showwarning("Campos obrigatorios", "Nome e URL base sao obrigatorios.", parent=win)
            result.clear()
            return
        result["active"] = active_var.get()
        win.destroy()

    def on_cancel() -> None:
        result.clear()
        win.destroy()

    ttk.Button(button_row, text="Salvar", command=on_save).grid(row=0, column=0, padx=4)
    ttk.Button(button_row, text="Cancelar", command=on_cancel).grid(row=0, column=1, padx=4)

    _center_on_parent(win, parent)
    win.wait_window()
    return result or None


def ask_new_competitor(parent: tk.Tk, can_map: bool) -> dict[str, Any] | None:
    """Pede so nome e URL de um concorrente novo. Retorna {'name','base_url','map_now'} ou None."""
    result: dict[str, Any] = {}
    win = tk.Toplevel(parent)
    win.title("Novo concorrente")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()

    frame = ttk.Frame(win, padding=12)
    frame.grid()
    ttk.Label(frame, text="Nome:").grid(row=0, column=0, sticky="w", pady=4)
    name_var = tk.StringVar()
    ttk.Entry(frame, textvariable=name_var, width=46).grid(row=0, column=1, pady=4)
    ttk.Label(frame, text="URL do site:").grid(row=1, column=0, sticky="w", pady=4)
    url_var = tk.StringVar()
    ttk.Entry(frame, textvariable=url_var, width=46).grid(row=1, column=1, pady=4)

    map_var = tk.BooleanVar(value=can_map)
    map_check = ttk.Checkbutton(
        frame, text="Mapear com IA agora (precisa de chave da API)", variable=map_var
    )
    map_check.grid(row=2, column=1, sticky="w", pady=6)
    if not can_map:
        map_var.set(False)
        map_check.state(["disabled"])

    button_row = ttk.Frame(frame)
    button_row.grid(row=3, column=0, columnspan=2, pady=(10, 0))

    def on_save() -> None:
        name = name_var.get().strip()
        url = url_var.get().strip()
        if not name or not url:
            messagebox.showwarning("Campos obrigatorios", "Informe nome e URL.", parent=win)
            return
        result["name"] = name
        result["base_url"] = url
        result["map_now"] = bool(map_var.get())
        win.destroy()

    def on_cancel() -> None:
        result.clear()
        win.destroy()

    ttk.Button(button_row, text="Salvar", command=on_save).grid(row=0, column=0, padx=4)
    ttk.Button(button_row, text="Cancelar", command=on_cancel).grid(row=0, column=1, padx=4)
    _center_on_parent(win, parent)
    win.wait_window()
    return result or None


def ask_import_sales_options(parent: tk.Tk) -> dict[str, Any] | None:
    """Pede o CSV de vendas + top N + modo. Retorna dict ou None."""
    result: dict[str, Any] = {}
    win = tk.Toplevel(parent)
    win.title("Importar vendas (CSV)")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()

    frame = ttk.Frame(win, padding=12)
    frame.grid()

    ttk.Label(frame, text="Arquivo CSV:").grid(row=0, column=0, sticky="w", pady=4)
    path_var = tk.StringVar()
    ttk.Entry(frame, textvariable=path_var, width=42).grid(row=0, column=1, pady=4)

    def browse() -> None:
        chosen = filedialog.askopenfilename(
            parent=win, title="Escolha o CSV de vendas",
            filetypes=[("CSV", "*.csv"), ("Todos", "*.*")],
        )
        if chosen:
            path_var.set(chosen)

    ttk.Button(frame, text="...", width=3, command=browse).grid(row=0, column=2, padx=4)

    ttk.Label(frame, text="Top N:").grid(row=1, column=0, sticky="w", pady=4)
    top_var = tk.StringVar(value="30")
    ttk.Entry(frame, textvariable=top_var, width=10).grid(row=1, column=1, sticky="w", pady=4)

    mode_var = tk.StringVar(value="most_sold")
    ttk.Radiobutton(frame, text="Mais vendidos", variable=mode_var, value="most_sold").grid(
        row=2, column=1, sticky="w"
    )
    ttk.Radiobutton(frame, text="Menos vendidos", variable=mode_var, value="least_sold").grid(
        row=3, column=1, sticky="w"
    )

    button_row = ttk.Frame(frame)
    button_row.grid(row=4, column=0, columnspan=3, pady=(12, 0))

    def on_ok() -> None:
        path = path_var.get().strip()
        if not path:
            messagebox.showwarning("Arquivo", "Escolha o CSV.", parent=win)
            return
        try:
            top_n = int(top_var.get().strip())
        except ValueError:
            messagebox.showwarning("Top N", "Informe um numero inteiro.", parent=win)
            return
        result["csv_path"] = path
        result["top_n"] = top_n
        result["mode"] = mode_var.get()
        win.destroy()

    def on_cancel() -> None:
        result.clear()
        win.destroy()

    ttk.Button(button_row, text="Importar", command=on_ok).grid(row=0, column=0, padx=4)
    ttk.Button(button_row, text="Cancelar", command=on_cancel).grid(row=0, column=1, padx=4)
    _center_on_parent(win, parent)
    win.wait_window()
    return result or None


def ask_single_book_title(parent: tk.Tk, titles: list[str]) -> str | None:
    """Pede um titulo da lista para coleta avulsa. Retorna o titulo ou None."""
    result: dict[str, str] = {}
    win = tk.Toplevel(parent)
    win.title("Coleta avulsa")
    win.resizable(False, False)
    win.transient(parent)
    win.grab_set()

    frame = ttk.Frame(win, padding=12)
    frame.grid()
    ttk.Label(frame, text="Escolha o livro:").grid(row=0, column=0, sticky="w", pady=4)
    title_var = tk.StringVar(value=titles[0] if titles else "")
    box = ttk.Combobox(frame, textvariable=title_var, values=titles, width=50, state="readonly")
    box.grid(row=1, column=0, pady=4)

    button_row = ttk.Frame(frame)
    button_row.grid(row=2, column=0, pady=(12, 0))

    def on_ok() -> None:
        chosen = title_var.get().strip()
        if not chosen:
            return
        result["title"] = chosen
        win.destroy()

    def on_cancel() -> None:
        result.clear()
        win.destroy()

    ttk.Button(button_row, text="Coletar", command=on_ok).grid(row=0, column=0, padx=4)
    ttk.Button(button_row, text="Cancelar", command=on_cancel).grid(row=0, column=1, padx=4)
    _center_on_parent(win, parent)
    win.wait_window()
    return result.get("title")
