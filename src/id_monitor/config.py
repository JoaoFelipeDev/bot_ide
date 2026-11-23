"""Carregamento de configuracao do projeto (caminhos e config.yaml de concorrentes)."""
from __future__ import annotations

import importlib
import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml
from ruamel.yaml import YAML

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
DATA_DIR = PROJECT_ROOT / "data"
REPORTS_DIR = PROJECT_ROOT / "reports"
LOGS_DIR = PROJECT_ROOT / "logs"
DB_PATH = DATA_DIR / "monitor.db"
COMPETITORS_CONFIG_PATH = CONFIG_DIR / "competitors.yaml"


@dataclass(frozen=True)
class CompetitorConfig:
    """Metadados de um concorrente, lidos de config/competitors.yaml.

    Alem dos dados basicos (key/name/base_url/module/class_name), cada concorrente carrega uma
    "receita" de busca generica baseada em CSS (search_url_template + seletores). Isso permite que
    a maioria dos sites seja suportada sem codigo customizado: basta ajustar os seletores no YAML
    apos inspecionar o HTML real do site. Sites com comportamento especial (paginacao via JS,
    busca que exige clique, etc.) podem sobrescrever o metodo `search()` na propria classe referenciada
    por module/class_name.
    """

    key: str
    name: str
    base_url: str
    module: str
    class_name: str
    active: bool = True
    search_url_template: str | None = None
    result_item_selector: str | None = None
    title_selector: str | None = None
    price_selector: str | None = None
    link_selector: str | None = None
    availability_selector: str | None = None
    # --- Mapeamento por IA (ver id_monitor.ai) ---------------------------------------------
    # search_mode: "url" (busca via search_url_template) ou "type" (digita no campo
    # search_input_selector da home e aperta Enter - para sites cuja busca nao muda a URL).
    search_mode: str = "url"
    search_input_selector: str | None = None
    mapped_by_ai: bool = False
    mapped_at: str | None = None
    # status: None (config manual), "ok", "fora_do_ar" ou "precisa_mapear".
    status: str | None = None

    def load_scraper_class(self) -> Any:
        """Importa dinamicamente a classe do scraper referenciada por module/class_name."""
        mod = importlib.import_module(self.module)
        return getattr(mod, self.class_name)

    @property
    def has_recipe(self) -> bool:
        """True se ha receita de busca suficiente para o scraper generico rodar."""
        if not self.result_item_selector:
            return False
        if self.search_mode == "type":
            return bool(self.search_input_selector)
        return bool(self.search_url_template)


def ensure_directories() -> None:
    """Garante que as pastas de dados/relatorios/logs existam."""
    for directory in (DATA_DIR, REPORTS_DIR, LOGS_DIR):
        directory.mkdir(parents=True, exist_ok=True)


def load_competitors(active_only: bool = True) -> list[CompetitorConfig]:
    """Le config/competitors.yaml e retorna a lista de concorrentes configurados."""
    if not COMPETITORS_CONFIG_PATH.exists():
        raise FileNotFoundError(
            f"Arquivo de configuracao de concorrentes nao encontrado: {COMPETITORS_CONFIG_PATH}"
        )

    with COMPETITORS_CONFIG_PATH.open("r", encoding="utf-8") as fh:
        raw = yaml.safe_load(fh) or {}

    competitors = [CompetitorConfig(**entry) for entry in raw.get("competitors", [])]

    if active_only:
        competitors = [c for c in competitors if c.active]

    return competitors


def get_competitor(key: str) -> CompetitorConfig:
    """Busca um unico concorrente pela chave (ex.: 'livraria_cultura')."""
    for competitor in load_competitors(active_only=False):
        if competitor.key == key:
            return competitor
    raise KeyError(f"Concorrente '{key}' nao encontrado em {COMPETITORS_CONFIG_PATH}")


# Campos que a interface grafica (ou qualquer chamador) pode atualizar em um concorrente
# existente. Nao inclui "key" (identificador, nao deve mudar) nem "module"/"class_name"
# (isso e codigo, nao configuracao - trocar exige editar o .py do scraper).
EDITABLE_COMPETITOR_FIELDS = (
    "name",
    "base_url",
    "active",
    "search_url_template",
    "result_item_selector",
    "title_selector",
    "price_selector",
    "link_selector",
    "availability_selector",
    "search_mode",
    "search_input_selector",
    "mapped_by_ai",
    "mapped_at",
    "status",
)

# Campos que formam a "receita de busca" de um concorrente (o que o mapeamento por IA gera e
# o que e salvo/restaurado nos backups).
RECIPE_FIELDS = (
    "search_mode",
    "search_input_selector",
    "search_url_template",
    "result_item_selector",
    "title_selector",
    "price_selector",
    "link_selector",
    "availability_selector",
    "mapped_by_ai",
    "mapped_at",
    "status",
)


def update_competitor(key: str, updates: dict[str, Any]) -> None:
    """Atualiza campos de um concorrente existente em config/competitors.yaml, preservando os
    comentarios e a formatacao do arquivo (usa ruamel.yaml em vez do PyYAML "comum" para isso).

    Usado pela interface grafica para permitir ajustar os seletores/URL de busca de um
    concorrente sem precisar editar o YAML manualmente. So aceita campos em
    `EDITABLE_COMPETITOR_FIELDS`; qualquer outra chave em `updates` e ignorada.
    """
    invalid = set(updates) - set(EDITABLE_COMPETITOR_FIELDS)
    if invalid:
        raise ValueError(f"Campos nao editaveis: {sorted(invalid)}")

    ryaml = YAML()
    ryaml.preserve_quotes = True

    with COMPETITORS_CONFIG_PATH.open("r", encoding="utf-8") as fh:
        data = ryaml.load(fh)

    entries = data.get("competitors", [])
    for entry in entries:
        if entry.get("key") == key:
            for field, value in updates.items():
                if value is None:
                    entry.pop(field, None)
                else:
                    entry[field] = value
            break
    else:
        raise KeyError(f"Concorrente '{key}' nao encontrado em {COMPETITORS_CONFIG_PATH}")

    with COMPETITORS_CONFIG_PATH.open("w", encoding="utf-8") as fh:
        ryaml.dump(data, fh)


def _slugify(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "_", normalized.lower()).strip("_")
    return slug or "concorrente"


def add_competitor(name: str, base_url: str, key: str | None = None) -> CompetitorConfig:
    """Cadastra um concorrente novo em config/competitors.yaml, so com nome e URL.

    O concorrente usa o scraper generico e nasce com status "precisa_mapear" (sem receita de
    busca): o mapeamento por IA (id_monitor.ai.mapper.map_competitor) preenche a receita depois.
    """
    name = name.strip()
    base_url = base_url.strip()
    if not name or not base_url:
        raise ValueError("Informe o nome e a URL do concorrente.")
    if not re.match(r"^https?://", base_url, re.IGNORECASE):
        base_url = "https://" + base_url

    existing_keys = {c.key for c in load_competitors(active_only=False)}
    base_key = _slugify(key or name)
    new_key = base_key
    counter = 2
    while new_key in existing_keys:
        new_key = f"{base_key}_{counter}"
        counter += 1

    ryaml = YAML()
    ryaml.preserve_quotes = True
    with COMPETITORS_CONFIG_PATH.open("r", encoding="utf-8") as fh:
        data = ryaml.load(fh)

    entry = {
        "key": new_key,
        "name": name,
        "base_url": base_url,
        "module": "id_monitor.scrapers.generic_css",
        "class_name": "GenericCssScraper",
        "active": True,
        "status": "precisa_mapear",
    }
    data["competitors"].append(entry)

    with COMPETITORS_CONFIG_PATH.open("w", encoding="utf-8") as fh:
        ryaml.dump(data, fh)

    return get_competitor(new_key)


# ----------------------------------------------------------------- backups de receita
def _backups_dir() -> Path:
    return COMPETITORS_CONFIG_PATH.parent / "backups"


def list_recipe_backups(key: str) -> list[Path]:
    """Backups de receita de um concorrente, do mais antigo para o mais recente."""
    directory = _backups_dir()
    if not directory.exists():
        return []
    return sorted(directory.glob(f"{key}__*.json"))


def backup_competitor_recipe(key: str) -> Path:
    """Salva a receita atual do concorrente em config/backups/ antes de ela ser sobrescrita."""
    competitor = get_competitor(key)
    recipe = {field: getattr(competitor, field) for field in RECIPE_FIELDS}
    directory = _backups_dir()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    # O relogio do Windows tem resolucao grossa: dois backups seguidos podem cair no mesmo
    # instante, entao um contador no fim do nome garante nome unico e ordem cronologica.
    sequence = 0
    path = directory / f"{key}__{stamp}_{sequence:03d}.json"
    while path.exists():
        sequence += 1
        path = directory / f"{key}__{stamp}_{sequence:03d}.json"
    payload = {"key": key, "saved_at": datetime.now().isoformat(timespec="seconds"), "recipe": recipe}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def restore_last_recipe_backup(key: str) -> Path:
    """Restaura o backup de receita mais recente do concorrente (e o consome, de modo que
    desfazer varias vezes volta cada vez mais no tempo). Levanta FileNotFoundError se nao ha."""
    backups = list_recipe_backups(key)
    if not backups:
        raise FileNotFoundError(f"Nao ha backup de receita para '{key}'.")
    latest = backups[-1]
    payload = json.loads(latest.read_text(encoding="utf-8"))
    update_competitor(key, {field: payload["recipe"].get(field) for field in RECIPE_FIELDS})
    latest.unlink()
    return latest
