"""Testes da chave de API (cofre, nunca em arquivo), das configuracoes de IA e do config.yaml
(novos campos, add_competitor e backups de receita)."""
from __future__ import annotations

from pathlib import Path

import pytest

from id_monitor import config as config_module
from id_monitor.ai import secrets as ai_secrets
from id_monitor.ai import settings as ai_settings
from id_monitor.ai.client import AIClientError, parse_json_object

SENTINEL_KEY = "sk-ant-TESTE-SENTINELA-1234567890"


@pytest.fixture
def temp_dirs(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    config_dir = tmp_path / "config"
    data_dir.mkdir()
    config_dir.mkdir()
    monkeypatch.setattr(config_module, "DATA_DIR", data_dir)
    monkeypatch.setattr(config_module, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_module, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(config_module, "REPORTS_DIR", tmp_path / "reports")
    monkeypatch.setattr(config_module, "COMPETITORS_CONFIG_PATH", config_dir / "competitors.yaml")
    return tmp_path


def write_competitors(tmp_path: Path, body: str | None = None) -> None:
    (tmp_path / "config" / "competitors.yaml").write_text(
        body
        or (
            "# comentario que precisa sobreviver\n"
            "competitors:\n"
            "  - key: loja_a\n"
            "    name: Loja A\n"
            "    base_url: https://loja-a.example\n"
            "    module: id_monitor.scrapers.generic_css\n"
            "    class_name: GenericCssScraper\n"
            "    active: true\n"
            "    status: precisa_mapear\n"
        ),
        encoding="utf-8",
    )


# ------------------------------------------------------------------- chave de API
def test_api_key_roundtrip_and_env_fallback(monkeypatch):
    assert ai_secrets.get_api_key() is None
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-env")
    assert ai_secrets.get_api_key() == "sk-ant-env"
    ai_secrets.set_api_key(f"  {SENTINEL_KEY}  ")
    assert ai_secrets.get_api_key() == SENTINEL_KEY  # o cofre tem prioridade sobre a env
    ai_secrets.delete_api_key()
    assert ai_secrets.get_api_key() == "sk-ant-env"
    ai_secrets.delete_api_key()  # remover sem ter chave nao deve explodir


def test_blank_key_is_rejected():
    with pytest.raises(ai_secrets.AIConfigError):
        ai_secrets.set_api_key("   ")


def test_mask_key_never_shows_the_whole_key():
    masked = ai_secrets.mask_key(SENTINEL_KEY)
    assert SENTINEL_KEY not in masked and masked.startswith("sk-ant-") and masked.endswith(SENTINEL_KEY[-4:])


def test_api_key_is_never_written_to_project_files(temp_dirs):
    write_competitors(temp_dirs)
    ai_secrets.set_api_key(SENTINEL_KEY)
    ai_settings.save_settings(ai_settings.AISettings(model="m", auto_repair=True))
    config_module.update_competitor("loja_a", {"status": "ok"})
    config_module.backup_competitor_recipe("loja_a")

    for path in temp_dirs.rglob("*"):
        if path.is_file():
            assert SENTINEL_KEY not in path.read_text(encoding="utf-8", errors="ignore"), path


# ----------------------------------------------------------------- configuracoes
def test_settings_defaults_and_roundtrip(temp_dirs):
    defaults = ai_settings.load_settings()
    assert defaults.model == ai_settings.DEFAULT_MODEL and defaults.auto_repair is True
    ai_settings.save_settings(ai_settings.AISettings(model="outro-modelo", auto_repair=False))
    loaded = ai_settings.load_settings()
    assert loaded.model == "outro-modelo" and loaded.auto_repair is False


def test_auto_repair_requires_key_and_flag(temp_dirs):
    assert ai_settings.auto_repair_enabled() is False  # sem chave
    ai_secrets.set_api_key(SENTINEL_KEY)
    assert ai_settings.auto_repair_enabled() is True
    ai_settings.save_settings(ai_settings.AISettings(auto_repair=False))
    assert ai_settings.auto_repair_enabled() is False


# ------------------------------------------------------------------- parse do JSON
def test_parse_json_object_tolerates_fences_and_prose():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Claro! {"a": {"b": 2}} espero ter ajudado') == {"a": {"b": 2}}
    with pytest.raises(AIClientError):
        parse_json_object("sem json aqui")
    with pytest.raises(AIClientError):
        parse_json_object("{quebrado: }")


# ---------------------------------------------------------------- config.yaml
def test_has_recipe_rules():
    base = dict(key="k", name="n", base_url="https://x", module="m", class_name="c")
    assert config_module.CompetitorConfig(**base).has_recipe is False
    assert config_module.CompetitorConfig(**base, result_item_selector="li", search_url_template="u").has_recipe
    assert not config_module.CompetitorConfig(**base, result_item_selector="li").has_recipe
    typed = config_module.CompetitorConfig(
        **base, result_item_selector="li", search_mode="type", search_input_selector="input"
    )
    assert typed.has_recipe is True


def test_add_competitor_generates_unique_key_and_keeps_comments(temp_dirs):
    write_competitors(temp_dirs)
    first = config_module.add_competitor("Livraria Nova Ação", "livraria-nova.example")
    second = config_module.add_competitor("Livraria Nova Ação", "https://outra.example")
    assert first.key == "livraria_nova_acao" and second.key == "livraria_nova_acao_2"
    assert first.base_url == "https://livraria-nova.example"
    assert first.status == "precisa_mapear" and first.has_recipe is False
    assert first.class_name == "GenericCssScraper"
    text = (temp_dirs / "config" / "competitors.yaml").read_text(encoding="utf-8")
    assert "# comentario que precisa sobreviver" in text
    with pytest.raises(ValueError):
        config_module.add_competitor("  ", "https://x.example")


def test_recipe_backup_and_restore_undo_in_order(temp_dirs):
    write_competitors(temp_dirs)
    config_module.update_competitor(
        "loja_a",
        {"search_url_template": "https://a/?q={query}", "result_item_selector": "li.v1", "status": "ok"},
    )
    config_module.backup_competitor_recipe("loja_a")  # guarda v1
    config_module.update_competitor("loja_a", {"result_item_selector": "li.v2", "mapped_by_ai": True})
    config_module.backup_competitor_recipe("loja_a")  # guarda v2
    config_module.update_competitor("loja_a", {"result_item_selector": "li.v3"})

    assert len(config_module.list_recipe_backups("loja_a")) == 2
    config_module.restore_last_recipe_backup("loja_a")
    restored = config_module.get_competitor("loja_a")
    assert restored.result_item_selector == "li.v2" and restored.mapped_by_ai is True
    config_module.restore_last_recipe_backup("loja_a")
    restored = config_module.get_competitor("loja_a")
    assert restored.result_item_selector == "li.v1" and restored.mapped_by_ai is False
    with pytest.raises(FileNotFoundError):
        config_module.restore_last_recipe_backup("loja_a")
