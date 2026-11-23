"""Testes do atualizador via GitHub Releases (sem rede)."""
from __future__ import annotations

import io
import zipfile

import pytest

from id_monitor.updater import (
    AvailableUpdate,
    UpdateError,
    UpdateSettings,
    apply_update,
    check_for_update,
    is_newer,
    load_update_settings,
    parse_version,
    updates_configured,
)


def test_parse_version_and_is_newer():
    assert parse_version("v0.2.1") == (0, 2, 1)
    assert parse_version("0.2.0") == (0, 2, 0)
    assert is_newer("0.2.1", "0.2.0")
    assert not is_newer("0.2.0", "0.2.0")
    assert not is_newer("0.1.9", "0.2.0")


def test_load_settings_rejects_placeholder(monkeypatch, tmp_path):
    cfg = tmp_path / "update.yaml"
    cfg.write_text('github_repo: "SEU_USUARIO/monitor-precos"\n', encoding="utf-8")
    monkeypatch.setattr("id_monitor.updater.UPDATE_CONFIG_PATH", cfg)
    monkeypatch.delenv("MONITOR_UPDATE_REPO", raising=False)
    with pytest.raises(UpdateError, match="nao esta configurada"):
        load_update_settings()
    assert updates_configured() is False


def test_load_settings_from_env(monkeypatch, tmp_path):
    cfg = tmp_path / "update.yaml"
    cfg.write_text('github_repo: "SEU_USUARIO/x"\n', encoding="utf-8")
    monkeypatch.setattr("id_monitor.updater.UPDATE_CONFIG_PATH", cfg)
    monkeypatch.setenv("MONITOR_UPDATE_REPO", "minha-org/monitor-precos")
    settings = load_update_settings()
    assert settings.github_repo == "minha-org/monitor-precos"


def test_check_for_update_when_newer(monkeypatch):
    release = {
        "tag_name": "v9.9.9",
        "html_url": "https://github.com/o/r/releases/tag/v9.9.9",
        "body": "notas",
        "assets": [
            {
                "name": "MonitorPrecosIDELivraria_2026.zip",
                "size": 1000,
                "browser_download_url": "https://example.com/pkg.zip",
            }
        ],
    }
    monkeypatch.setattr("id_monitor.updater._http_get_json", lambda _url: release)
    settings = UpdateSettings(github_repo="minha-org/monitor-precos")
    update = check_for_update(settings)
    assert update is not None
    assert update.new_version == "9.9.9"
    assert update.asset_name.endswith(".zip")


def test_check_for_update_when_same(monkeypatch):
    from id_monitor import __version__

    release = {
        "tag_name": f"v{__version__}",
        "html_url": "https://github.com/o/r/releases/tag/v1",
        "body": "",
        "assets": [
            {
                "name": "MonitorPrecosIDELivraria.zip",
                "size": 10,
                "browser_download_url": "https://example.com/a.zip",
            }
        ],
    }
    monkeypatch.setattr("id_monitor.updater._http_get_json", lambda _url: release)
    settings = UpdateSettings(github_repo="minha-org/monitor-precos")
    assert check_for_update(settings) is None


def test_apply_update_preserves_data(tmp_path, monkeypatch):
    project = tmp_path / "app"
    project.mkdir()
    (project / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    (project / "src").mkdir()
    data = project / "data"
    data.mkdir()
    (data / "keep.db").write_text("user", encoding="utf-8")
    cfg = project / "config"
    cfg.mkdir()
    (cfg / "update.yaml").write_text("github_repo: minha-org/r\n", encoding="utf-8")
    (cfg / "competitors.yaml").write_text("competitors: []\n", encoding="utf-8")

    # Pacote novo: src novo + tenta sobrescrever data/ e update.yaml
    pkg = tmp_path / "pkg"
    (pkg / "src").mkdir(parents=True)
    (pkg / "src" / "novo.py").write_text("ok", encoding="utf-8")
    (pkg / "pyproject.toml").write_text("[project]\nname='x'\nversion='9'\n", encoding="utf-8")
    (pkg / "data").mkdir()
    (pkg / "data" / "keep.db").write_text("NAO", encoding="utf-8")
    (pkg / "config").mkdir()
    (pkg / "config" / "update.yaml").write_text("github_repo: SEU_USUARIO/x\n", encoding="utf-8")
    (pkg / "config" / "other.yaml").write_text("novo\n", encoding="utf-8")

    zip_path = tmp_path / "MonitorPrecosIDELivraria_test.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        for f in pkg.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(pkg))

    monkeypatch.setattr("id_monitor.updater.PROJECT_ROOT", project)

    class StreamResp:
        def __enter__(self):
            return io.BytesIO(zip_path.read_bytes())

        def __exit__(self, *a):
            return False

    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: StreamResp())
    monkeypatch.setattr("id_monitor.updater._reinstall_package", lambda progress: progress("skip pip"))

    update = AvailableUpdate(
        current_version="0.1.0",
        new_version="9.9.9",
        tag_name="v9.9.9",
        download_url="https://example.com/pkg.zip",
        asset_name="MonitorPrecosIDELivraria_test.zip",
        release_notes="",
        html_url="",
    )
    apply_update(update, progress=lambda _m: None)

    assert (data / "keep.db").read_text(encoding="utf-8") == "user"
    assert (cfg / "update.yaml").read_text(encoding="utf-8").startswith("github_repo: minha-org")
    assert (cfg / "competitors.yaml").exists()
    assert (project / "src" / "novo.py").read_text(encoding="utf-8") == "ok"
    assert (cfg / "other.yaml").read_text(encoding="utf-8") == "novo\n"
