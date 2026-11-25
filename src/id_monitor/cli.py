"""CLI do Monitor de Precos IDE Livraria.

Comandos:
  id-monitor add-book     Adiciona/atualiza um livro (atualiza so se o ISBN ja existir).
  id-monitor list-books   Lista os livros atualmente monitorados (ativos).
  id-monitor import-sales Importa um CSV de vendas e seleciona automaticamente os top N titulos.
  id-monitor import-catalog Importa titulos, ISBN e precos do catalogo da IDE Editora (Firestore).
  id-monitor collect      Roda a coleta de precos nos concorrentes (todos os produtos ativos,
                           ou apenas um livro especifico via --book, para coletas avulsas).
  id-monitor report       Gera o relatorio Excel comparativo mais recente.
  id-monitor smoke-test   Checa rapidamente se os scrapers ainda estao funcionando (sem
                           gravar nada no historico de precos).
  id-monitor set-api-key  Guarda a chave da API do Claude no cofre de credenciais do Windows.
  id-monitor add-site     Cadastra um concorrente novo so com nome e URL.
  id-monitor map-site     Mapeia um concorrente com a IA (descobre a busca e os seletores).
  id-monitor check-update Verifica se ha versao nova no GitHub (Release publica).
  id-monitor self-update  Baixa e aplica a atualizacao (preserva data/, logs, lista de livros).
  id-monitor sync-books   Sincroniza livros com o Firestore compartilhado.
  id-monitor send-report-email  Envia o Excel por e-mail (Resend).
  id-monitor run-scheduled      Ciclo completo: collect + report + sync + e-mail.

Exemplos:
  id-monitor add-book --title "O Pequeno Principe" --isbn 9788574061346 --price 24.90
  id-monitor import-sales vendas_outubro.csv --top-n 30
  id-monitor collect
  id-monitor collect --book "O Pequeno Principe"
  id-monitor report
"""
from __future__ import annotations

from pathlib import Path

import click

from id_monitor.catalog_import import (
    DEFAULT_MIN_PRICE,
    CatalogError,
    fetch_catalog,
    import_catalog,
    select_items,
)
from id_monitor.collector import collect_prices
from id_monitor.reporting.report import build_report
from id_monitor.sales_import import import_sales_csv
from id_monitor.smoke_test import DEFAULT_PROBE_QUERY, run_smoke_test
from id_monitor.storage.db import Product, ProductOrigin, get_session, init_db, sync_competitors_from_config


@click.group()
def cli() -> None:
    """Monitor de precos de concorrentes para a IDE Livraria."""
    init_db()
    session = get_session()
    sync_competitors_from_config(session)


@cli.command("add-book")
@click.option("--title", required=True, help="Titulo do livro.")
@click.option("--author", default=None, help="Autor do livro.")
@click.option("--isbn", default=None, help="ISBN do livro (recomendado: distingue edicoes/editoras).")
@click.option("--sku", default=None, help="SKU interno do livro, se houver.")
@click.option("--price", "price", type=float, default=None, help="Seu preco de referencia para comparar.")
def add_book(title: str, author: str | None, isbn: str | None, sku: str | None, price: float | None) -> None:
    """Adiciona um livro manualmente, ou atualiza se o mesmo ISBN ja existir (titulo nao conta)."""
    from id_monitor.matching.isbn import normalize_isbn_digits
    from id_monitor.storage.db import find_product_by_isbn

    isbn = normalize_isbn_digits(isbn) if isbn else None
    session = get_session()
    existing = find_product_by_isbn(session, isbn)
    if existing is None:
        existing = Product(
            title=title, author=author, isbn=isbn, sku=sku, price_id_livraria=price,
            origin=ProductOrigin.MANUAL, active=True,
        )
        session.add(existing)
        click.echo(f"Livro adicionado: {title}")
    else:
        existing.title = title or existing.title
        existing.author = author or existing.author
        existing.isbn = isbn or existing.isbn
        existing.sku = sku or existing.sku
        if price is not None:
            existing.price_id_livraria = price
        existing.active = True
        click.echo(f"Livro atualizado (mesmo ISBN {isbn}): {title}")
    session.commit()
    from id_monitor.cloud.sync_books import push_product

    push_product(existing)


@cli.command("list-books")
def list_books() -> None:
    """Lista os livros atualmente ativos na lista de monitoramento."""
    session = get_session()
    products = session.query(Product).filter(Product.active.is_(True)).order_by(Product.title).all()
    if not products:
        click.echo("Nenhum livro monitorado no momento.")
        return
    for product in products:
        preco = f"R$ {product.price_id_livraria:.2f}" if product.price_id_livraria else "sem preco"
        click.echo(f"- {product.title} | ISBN: {product.isbn or '-'} | {preco} | origem: {product.origin.value}")


@cli.command("import-sales")
@click.argument("csv_path", type=click.Path(exists=True, path_type=Path))
@click.option("--top-n", default=30, show_default=True, help="Quantidade de titulos a selecionar.")
@click.option(
    "--mode",
    type=click.Choice(["most_sold", "least_sold"]),
    default="most_sold",
    show_default=True,
    help="Selecionar os mais vendidos ou os menos vendidos.",
)
def import_sales(csv_path: Path, top_n: int, mode: str) -> None:
    """Importa um CSV de vendas e atualiza a lista de monitoramento automaticamente (top N)."""
    selected = import_sales_csv(csv_path, top_n=top_n, mode=mode)
    click.echo(f"{len(selected)} titulo(s) selecionado(s) e adicionados/atualizados na lista de monitoramento.")
    for product in selected:
        click.echo(f"  - {product.title}")


@cli.command("import-catalog")
@click.option("--only-update", is_flag=True, help="So atualiza preco/ISBN de livros ja cadastrados (nao adiciona novos).")
@click.option("--list-price", is_flag=True, help="Usa o preco de tabela, sem o desconto da loja.")
@click.option("--min-price", type=float, default=DEFAULT_MIN_PRICE, show_default=True,
              help="Ignora itens abaixo desse valor (brindes/amostras).")
@click.option("--dry-run", is_flag=True, help="Mostra o que seria importado, sem gravar nada.")
def import_catalog_cmd(only_update: bool, list_price: bool, min_price: float, dry_run: bool) -> None:
    """Importa titulos, ISBN e precos do catalogo da IDE Editora (Firestore, so leitura)."""
    try:
        items = fetch_catalog()
    except CatalogError as exc:
        raise click.ClickException(str(exc)) from exc

    if dry_run:
        accepted, skipped = select_items(items, min_price=min_price)
        click.echo(f"{len(accepted)} livro(s) seriam importados; {len(skipped)} ignorado(s).")
        for item in accepted:
            price = item.list_price if list_price else item.final_price
            click.echo(f"  + {item.title} | ISBN: {item.isbn or '-'} | R$ {price:.2f}")
        for title, reason in skipped:
            click.echo(f"  - {title} ({reason})")
        return

    report = import_catalog(
        items, add_new=not only_update, use_final_price=not list_price, min_price=min_price
    )
    click.echo(
        f"Catalogo importado: {len(report.added)} adicionado(s), {len(report.updated)} atualizado(s), "
        f"{len(report.skipped)} ignorado(s)."
    )
    for title, reason in report.skipped:
        click.echo(f"  - ignorado: {title} ({reason})")


@cli.command("collect")
@click.option(
    "--book", "books", multiple=True,
    help="Titulo de um livro especifico para coleta avulsa (pode repetir a opcao). "
         "Se omitido, coleta todos os livros ativos.",
)
@click.option(
    "--competitor", "competitors", multiple=True,
    help="Key do concorrente (ver config/competitors.yaml) para restringir a coleta. "
         "Se omitido, usa todos os concorrentes ativos.",
)
def collect(books: tuple[str, ...], competitors: tuple[str, ...]) -> None:
    """Roda a coleta de precos nos sites concorrentes."""
    count = collect_prices(
        product_titles=list(books) or None,
        competitor_keys=list(competitors) or None,
    )
    click.echo(f"Coleta concluida: {count} registro(s) gravados em price_history.")


@cli.command("report")
@click.option("--output", "output_path", type=click.Path(path_type=Path), default=None, help="Caminho do arquivo .xlsx de saida.")
def report(output_path: Path | None) -> None:
    """Gera o relatorio Excel comparativo mais recente (reports/relatorio_precos_<data>.xlsx)."""
    path = build_report(output_path)
    click.echo(f"Relatorio gerado em: {path}")


@cli.command("smoke-test")
@click.option("--query", default=DEFAULT_PROBE_QUERY, show_default=True, help="Termo de busca usado no diagnostico.")
def smoke_test(query: str) -> None:
    """Roda uma busca de diagnostico em cada concorrente ativo, sem gravar nada no historico,
    para checar rapidamente se algum scraper quebrou (ex.: site mudou o layout)."""
    results = run_smoke_test(probe_query=query)
    click.echo(f"Smoke-test com o termo '{query}':")
    any_issue = False
    for result in results:
        click.echo(f"  - {result.competitor_name}: {result.status_label}")
        if not result.ok:
            any_issue = True
    if any_issue:
        click.echo("\nAtencao: pelo menos um concorrente parece com problema. Verifique os "
                    "seletores em config/competitors.yaml comparando com o HTML real do site.")


@cli.command("set-api-key")
@click.option("--key", "api_key", prompt="Chave da API do Claude", hide_input=True, help="Chave da API (sk-ant-...).")
def set_api_key_cmd(api_key: str) -> None:
    """Salva a chave da API do Claude no Cofre de Credenciais do Windows (nunca em arquivo)."""
    from id_monitor.ai.secrets import AIConfigError, set_api_key

    try:
        set_api_key(api_key)
    except AIConfigError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo("Chave salva no cofre de credenciais.")


@cli.command("add-site")
@click.argument("name")
@click.argument("url")
@click.option("--map/--no-map", "do_map", default=False, help="Mapear o site com a IA logo apos cadastrar.")
def add_site(name: str, url: str, do_map: bool) -> None:
    """Cadastra um concorrente novo so com nome e URL (a receita de busca vem do mapeamento)."""
    from id_monitor.config import add_competitor

    try:
        competitor = add_competitor(name, url)
    except ValueError as exc:
        raise click.ClickException(str(exc)) from exc
    sync_competitors_from_config(get_session())
    click.echo(f"Concorrente cadastrado: {competitor.name} (chave: {competitor.key}).")
    if do_map:
        _run_map(competitor.key)
    else:
        click.echo(f"Para mapear com a IA: id-monitor map-site {competitor.key}")


@cli.command("map-site")
@click.argument("key")
def map_site(key: str) -> None:
    """Mapeia (ou re-mapeia) um concorrente com a IA: descobre a busca e os seletores, valida
    com uma busca de teste e salva a receita em config/competitors.yaml."""
    _run_map(key)


def _run_map(key: str) -> None:
    from id_monitor.ai.client import AIClientError
    from id_monitor.ai.mapper import map_competitor
    from id_monitor.ai.secrets import AIConfigError

    try:
        result = map_competitor(key, progress=click.echo)
    except KeyError as exc:
        raise click.ClickException(str(exc.args[0])) from exc
    except (AIConfigError, AIClientError) as exc:
        raise click.ClickException(str(exc)) from exc

    if result.success:
        click.echo(f"OK: {result.message}")
    else:
        raise click.ClickException(f"Nao foi possivel mapear '{key}': {result.message}")


@cli.command("check-update")
def check_update_cmd() -> None:
    """Consulta a Release mais recente no GitHub e diz se ha versao nova."""
    from id_monitor.updater import UpdateError, check_for_update, current_version, load_update_settings

    click.echo(f"Versao instalada: {current_version()}")
    try:
        settings = load_update_settings()
        update = check_for_update(settings)
    except UpdateError as exc:
        raise click.ClickException(str(exc)) from exc
    if update is None:
        click.echo(f"Ja esta na versao mais recente (repo {settings.github_repo}).")
        return
    click.echo(f"Nova versao disponivel: {update.new_version} (tag {update.tag_name})")
    click.echo(f"Pacote: {update.asset_name}")
    click.echo(f"Pagina: {update.html_url}")
    click.echo("Para aplicar: id-monitor self-update")


@cli.command("self-update")
@click.option("--yes", is_flag=True, help="Nao pedir confirmacao.")
def self_update_cmd(yes: bool) -> None:
    """Baixa e aplica a atualizacao mais recente do GitHub, preservando data/ e sua lista."""
    from id_monitor.updater import UpdateError, apply_update, check_for_update, load_update_settings

    try:
        settings = load_update_settings()
        update = check_for_update(settings)
    except UpdateError as exc:
        raise click.ClickException(str(exc)) from exc
    if update is None:
        click.echo("Nada para atualizar.")
        return
    click.echo(f"Atualizar {update.current_version} -> {update.new_version}?")
    if not yes and not click.confirm("Continuar"):
        click.echo("Cancelado.")
        return
    try:
        apply_update(update, progress=click.echo)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(str(exc)) from exc
    click.echo("Feche e abra o programa para usar a versao nova.")


@cli.command("sync-books")
@click.option("--push", "direction", flag_value="push", help="Envia a lista local para o Firestore.")
@click.option("--pull", "direction", flag_value="pull", default=True, help="Puxa a lista do Firestore (padrao).")
def sync_books_cmd(direction: str) -> None:
    """Sincroniza livros monitorados com o Firestore compartilhado."""
    from id_monitor.cloud.settings import cloud_configured
    from id_monitor.cloud.sync_books import pull_books_into_local, push_product

    if not cloud_configured():
        raise click.ClickException("Nuvem nao configurada (config/cloud.yaml + JSON da service account).")
    if direction == "push":
        session = get_session()
        products = session.query(Product).all()
        for product in products:
            push_product(product)
        click.echo(f"Enviados {len(products)} livro(s) ao Firestore.")
        return
    n = pull_books_into_local(progress=click.echo)
    click.echo(f"Sync concluido ({n} item(ns)).")


@cli.command("send-report-email")
@click.option("--report", "report_path", type=click.Path(exists=True, path_type=Path), default=None,
              help="Caminho do Excel; se omitido, gera um relatorio novo.")
def send_report_email_cmd(report_path: Path | None) -> None:
    """Envia o relatorio Excel por e-mail via Resend."""
    from id_monitor.notify.email_resend import EmailError, send_report_email

    path = report_path or build_report()
    try:
        result = send_report_email(path)
    except EmailError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"OK: {result.message} id={result.provider_id}")


@cli.command("run-scheduled")
def run_scheduled_cmd() -> None:
    """Ciclo completo: collect + report + sync Firestore + e-mail (usado pelo Task Scheduler)."""
    from id_monitor.schedule_cycle import run_scheduled_cycle

    try:
        result = run_scheduled_cycle(progress=click.echo)
    except Exception as exc:  # noqa: BLE001
        raise click.ClickException(str(exc)) from exc
    click.echo(
        f"Ciclo OK: run={result.run_id} registros={result.records_written} "
        f"email={result.email_status} relatorio={result.report_path}"
    )


if __name__ == "__main__":
    cli()
