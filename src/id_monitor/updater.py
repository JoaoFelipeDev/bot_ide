"""Atualizacao automatica a partir de um repositorio publico no GitHub (Releases).

Fluxo:
  1. Le a versao atual (`id_monitor.__version__`).
  2. Consulta `GET /repos/{owner}/{repo}/releases/latest` (sem autenticao, repo publico).
  3. Se a tag (ex.: v0.2.1) for maior que a versao instalada, baixa o .zip da release.
  4. Extrai por cima da pasta do programa, preservando dados do usuario (.venv, data, logs,
     reports, competitors.yaml e backups).
  5. Roda `pip install -e .` no ambiente virtual para atualizar entry points.

Configure o repo em `config/update.yaml` (ou na variavel de ambiente MONITOR_UPDATE_REPO).
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import ssl
import subprocess
import tempfile
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import yaml

from id_monitor import __version__ as CURRENT_VERSION
from id_monitor.config import CONFIG_DIR, PROJECT_ROOT

logger = logging.getLogger(__name__)

Progress = Callable[[str], None]
UPDATE_CONFIG_PATH = CONFIG_DIR / "update.yaml"
USER_AGENT = f"id-monitor-updater/{CURRENT_VERSION}"


def _ssl_context() -> ssl.SSLContext:
    """Contexto SSL com CAs do pacote certifi (evita CERTIFICATE_VERIFY_FAILED no Windows)."""
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001 - cai no store padrao do sistema
        return ssl.create_default_context()


def _urlopen(request: urllib.request.Request, *, timeout: float):
    return urllib.request.urlopen(request, timeout=timeout, context=_ssl_context())


def _network_error_message(exc: BaseException) -> str:
    text = str(exc)
    if "CERTIFICATE_VERIFY_FAILED" in text or "SSL" in text.upper():
        return (
            "Falha de certificado SSL ao falar com o GitHub "
            "(comum em alguns Pythons no Windows). "
            "Atualize para a versao mais recente do programa ou rode instalar.bat de novo. "
            f"Detalhe: {exc}"
        )
    return f"Sem internet ou GitHub inacessivel: {exc}"

# Pastas/arquivos do usuario que NAO devem ser sobrescritos pela atualizacao.
PRESERVE_NAMES = {
    ".venv",
    "data",
    "logs",
    "reports",
    ".git",
    ".cursor",
}
PRESERVE_RELATIVE_FILES = {
    Path("config") / "competitors.yaml",
    Path("config") / "update.yaml",  # permite o cliente manter o repo apontado
}


@dataclass(frozen=True)
class UpdateSettings:
    github_repo: str
    asset_name_contains: str = "MonitorPrecosIDELivraria"
    check_on_startup: bool = True


@dataclass(frozen=True)
class AvailableUpdate:
    current_version: str
    new_version: str
    tag_name: str
    download_url: str
    asset_name: str
    release_notes: str
    html_url: str


class UpdateError(RuntimeError):
    """Falha ao verificar ou aplicar atualizacao."""


def _looks_like_placeholder(repo: str) -> bool:
    low = repo.lower()
    return (
        not repo
        or "/" not in repo
        or "seu_usuario" in low
        or "owner/" in low
        or "nome-do-repo" in low
        or low.startswith("exemplo/")
    )


def load_update_settings() -> UpdateSettings:
    """Le config/update.yaml; MONITOR_UPDATE_REPO (owner/repo) sobrescreve o yaml se existir."""
    repo = (os.environ.get("MONITOR_UPDATE_REPO") or "").strip()
    asset = "MonitorPrecosIDELivraria"
    check_startup = True
    if UPDATE_CONFIG_PATH.exists():
        raw = yaml.safe_load(UPDATE_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        repo = repo or str(raw.get("github_repo") or "").strip()
        asset = str(raw.get("asset_name_contains") or asset).strip() or asset
        check_startup = bool(raw.get("check_on_startup", True))
    if _looks_like_placeholder(repo):
        # Placeholder ainda nao configurado: atualizacao desligada ate apontar um repo real.
        raise UpdateError(
            "Atualizacao automatica ainda nao esta configurada. "
            "Edite config/update.yaml e coloque o repositorio publico "
            "(ex.: minha-org/monitor-precos) ou defina MONITOR_UPDATE_REPO."
        )
    return UpdateSettings(github_repo=repo, asset_name_contains=asset, check_on_startup=check_startup)


def updates_configured() -> bool:
    """True se config/update.yaml (ou env) aponta para um repo real."""
    try:
        load_update_settings()
        return True
    except UpdateError:
        return False


def parse_version(text: str) -> tuple[int, ...]:
    """Extrai tupla numerica de 'v0.2.1' / '0.2.1' / '0.2.1-beta' -> (0, 2, 1)."""
    match = re.search(r"(\d+(?:\.\d+)*)", text.strip())
    if not match:
        raise UpdateError(f"Versao nao reconhecida: {text!r}")
    return tuple(int(part) for part in match.group(1).split("."))


def is_newer(candidate: str, current: str = CURRENT_VERSION) -> bool:
    return parse_version(candidate) > parse_version(current)


def _http_get_json(url: str) -> dict:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": USER_AGENT,
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    try:
        with _urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError(
                "Nao achei releases neste repositorio. Publique uma Release no GitHub "
                "(tag vX.Y.Z + arquivo .zip)."
            ) from exc
        raise UpdateError(f"GitHub respondeu HTTP {exc.code} ao consultar atualizacoes.") from exc
    except urllib.error.URLError as exc:
        raise UpdateError(_network_error_message(exc)) from exc
    except ssl.SSLError as exc:
        raise UpdateError(_network_error_message(exc)) from exc


def _pick_asset(release: dict, name_contains: str) -> dict:
    assets = release.get("assets") or []
    zip_assets = [
        a for a in assets
        if str(a.get("name", "")).lower().endswith(".zip")
        and name_contains.lower() in str(a.get("name", "")).lower()
    ]
    if not zip_assets:
        zip_assets = [a for a in assets if str(a.get("name", "")).lower().endswith(".zip")]
    if not zip_assets:
        raise UpdateError(
            "A release mais recente nao tem um arquivo .zip anexado. "
            "Anexe o pacote gerado por scripts/criar_pacote_zip.ps1."
        )
    # Prefere o maior (pacote completo) se houver varios.
    return max(zip_assets, key=lambda a: int(a.get("size") or 0))


def check_for_update(settings: UpdateSettings | None = None) -> AvailableUpdate | None:
    """Consulta o GitHub. Devolve AvailableUpdate se houver versao nova; senao None."""
    settings = settings or load_update_settings()
    url = f"https://api.github.com/repos/{settings.github_repo}/releases/latest"
    release = _http_get_json(url)
    tag = str(release.get("tag_name") or "")
    if not tag:
        raise UpdateError("A release mais recente nao tem tag_name.")
    if not is_newer(tag, CURRENT_VERSION):
        return None
    asset = _pick_asset(release, settings.asset_name_contains)
    download_url = asset.get("browser_download_url")
    if not download_url:
        raise UpdateError("O asset da release nao tem URL de download.")
    version_match = re.search(r"(\d+(?:\.\d+)*)", tag)
    if not version_match:
        raise UpdateError(f"Tag da release nao tem numero de versao: {tag!r}")
    return AvailableUpdate(
        current_version=CURRENT_VERSION,
        new_version=version_match.group(1),
        tag_name=tag,
        download_url=str(download_url),
        asset_name=str(asset.get("name") or "update.zip"),
        release_notes=str(release.get("body") or "").strip(),
        html_url=str(release.get("html_url") or ""),
    )


def _download(url: str, dest: Path, progress: Progress) -> None:
    progress(f"Baixando {dest.name}...")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with _urlopen(request, timeout=120) as response, dest.open("wb") as out:
            shutil.copyfileobj(response, out)
    except (urllib.error.URLError, ssl.SSLError) as exc:
        raise UpdateError(_network_error_message(exc)) from exc


def _should_preserve(relative: Path) -> bool:
    if relative.parts and relative.parts[0] in PRESERVE_NAMES:
        return True
    return relative in PRESERVE_RELATIVE_FILES


def _copy_update_tree(source_root: Path, target_root: Path, progress: Progress) -> None:
    """Copia o conteudo do pacote novo, pulando pastas/arquivos preservados."""
    # O zip pode ter os arquivos na raiz ou dentro de uma unica pasta.
    children = [p for p in source_root.iterdir() if p.name not in (".", "..")]
    if len(children) == 1 and children[0].is_dir() and not (children[0] / "pyproject.toml").exists():
        # pasta embrulho sem pyproject: desce um nivel se o pyproject estiver dentro
        inner = children[0]
        if (inner / "pyproject.toml").exists() or (inner / "instalar.bat").exists():
            source_root = inner
            children = list(source_root.iterdir())

    if not (source_root / "pyproject.toml").exists() and not (source_root / "src").exists():
        # tenta achar a pasta que tem o projeto
        for candidate in source_root.rglob("pyproject.toml"):
            source_root = candidate.parent
            break

    progress("Aplicando arquivos novos (preservando seus dados)...")
    for src in source_root.rglob("*"):
        rel = src.relative_to(source_root)
        if _should_preserve(rel):
            continue
        dest = target_root / rel
        if src.is_dir():
            dest.mkdir(parents=True, exist_ok=True)
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)


def _reinstall_package(progress: Progress) -> None:
    venv_python = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if not venv_python.exists():
        progress("Ambiente .venv nao encontrado; rode instalar.bat depois de reiniciar.")
        return
    progress("Atualizando o ambiente Python (pip install)...")
    subprocess.run(
        [str(venv_python), "-m", "pip", "install", "-e", str(PROJECT_ROOT)],
        check=True,
        cwd=str(PROJECT_ROOT),
    )


def apply_update(update: AvailableUpdate, progress: Progress | None = None) -> Path:
    """Baixa e aplica a atualizacao. Retorna o caminho do zip baixado (em TEMP)."""
    progress = progress or (lambda _m: None)
    with tempfile.TemporaryDirectory(prefix="id_monitor_update_") as tmp:
        tmp_path = Path(tmp)
        zip_path = tmp_path / update.asset_name
        _download(update.download_url, zip_path, progress)

        extract_dir = tmp_path / "extracted"
        extract_dir.mkdir()
        progress("Extraindo pacote...")
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_dir)

        _copy_update_tree(extract_dir, PROJECT_ROOT, progress)
        _reinstall_package(progress)

    progress(
        f"Atualizacao para {update.new_version} aplicada. Feche e abra o programa de novo."
    )
    return PROJECT_ROOT


def current_version() -> str:
    return CURRENT_VERSION
