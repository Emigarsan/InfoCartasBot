import subprocess
import sys
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple


CONFIG_FILES_TO_COPY = ["factions.json", "sets.json", "types.json", "packs.json"]


def _run_command(command: List[str], cwd: Optional[Path] = None, timeout: int = 300) -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd else None,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
        )
        return True, (result.stdout or "").strip()
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        message = stderr or stdout or str(exc)
        return False, message


def _current_branch(repo_dir: Path) -> str:
    ok, branch = _run_command(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_dir)
    if ok and branch and branch != "HEAD":
        return branch
    return "main"


def _remote_has_branch(repo_dir: Path, branch: str) -> bool:
    ok, output = _run_command(["git", "ls-remote", "--heads", "origin", branch], cwd=repo_dir)
    return ok and bool(output.strip())


def _resolve_remote_branch(repo_dir: Path) -> str:
    candidates: List[str] = []
    local_branch = _current_branch(repo_dir)
    candidates.append(local_branch)
    for fallback in ["main", "master"]:
        if fallback not in candidates:
            candidates.append(fallback)

    for branch in candidates:
        if _remote_has_branch(repo_dir, branch):
            return branch

    return local_branch


def _repo_has_remote_changes(repo_dir: Path) -> Tuple[bool, bool, str]:
    if not repo_dir.exists():
        return False, False, f"Repositorio no encontrado: {repo_dir}"

    ok, output = _run_command(["git", "status", "--porcelain"], cwd=repo_dir)
    if not ok:
        return False, False, f"No se pudo comprobar estado git en {repo_dir.name}: {output}"
    if output:
        return False, False, f"Repositorio con cambios locales en {repo_dir.name}. Cancela pull por seguridad."

    ok, output = _run_command(["git", "fetch", "origin"], cwd=repo_dir)
    if not ok:
        return False, False, f"No se pudo hacer fetch en {repo_dir.name}: {output}"

    branch = _resolve_remote_branch(repo_dir)
    ok, output = _run_command(["git", "rev-list", "--left-right", f"HEAD...origin/{branch}"], cwd=repo_dir)
    if not ok:
        return False, False, f"No se pudo comparar ramas en {repo_dir.name}: {output}"

    has_changes = bool(output.strip())
    return True, has_changes, ""


def _pull_repo(repo_dir: Path) -> Tuple[bool, str]:
    branch = _resolve_remote_branch(repo_dir)
    return _run_command(["git", "pull", "origin", branch], cwd=repo_dir)


def _run_generation_scripts(workspace_dir: Path) -> Tuple[bool, str]:
    pipeline_dir = workspace_dir / "pipeline"

    ok, output = _run_command([sys.executable, "json_traducido.py"], cwd=pipeline_dir, timeout=1800)
    if not ok:
        return False, f"Error en json_traducido.py: {output}"

    ok, output = _run_command([sys.executable, "merge_json.py"], cwd=pipeline_dir, timeout=1800)
    if not ok:
        return False, f"Error en merge_json.py: {output}"

    return True, "Scripts de generación ejecutados correctamente"


def _copy_generated_files(workspace_dir: Path) -> Tuple[bool, str]:
    pipeline_dir = workspace_dir / "pipeline"
    data_dir = workspace_dir / "bot" / "data"

    source_json = pipeline_dir / "marvelcdb_spanish.json"
    target_json = data_dir / "marvelcdb_spanish.json"
    if not source_json.exists():
        return False, f"No existe archivo generado: {source_json}"
    shutil.copy2(source_json, target_json)

    source_config_dir = workspace_dir / "pipeline" / "marvelsdb-json-data" / "translations" / "es"
    if not source_config_dir.exists():
        return False, f"No existe directorio de configuración: {source_config_dir}"

    for file_name in CONFIG_FILES_TO_COPY:
        source = source_config_dir / file_name
        target = data_dir / file_name
        if not source.exists():
            return False, f"Falta archivo de configuración: {source}"
        shutil.copy2(source, target)

    return True, "JSON y configuraciones copiadas a bot/data"


def _run_image_pipeline(workspace_dir: Path) -> Tuple[bool, str]:
    """Genera imagenes con marca de agua y las sube a Telegram."""
    data_dir = workspace_dir / "bot" / "data"

    ok, output = _run_command([sys.executable, "marca_agua_v2.py"], cwd=data_dir, timeout=3600)
    if not ok:
        return False, f"Error en marca_agua_v2.py: {output}"

    ok, output = _run_command([sys.executable, "telegram_imagenes.py"], cwd=data_dir, timeout=7200)
    if not ok:
        return False, f"Error en telegram_imagenes.py: {output}"

    return True, "Imagenes con marca de agua generadas y subidas a Telegram"


def run_soft_update(base_dir: Path) -> Dict[str, object]:
    """
    Actualización en caliente sin reiniciar servicios.

    Usa base_dir del bot (bot/) para resolver rutas al workspace raíz.
    """
    workspace_dir = base_dir.parent
    repos = [
        workspace_dir / "pipeline" / "marvelsdb-json-data",
        workspace_dir / "freakmod",
    ]

    messages: List[str] = []
    updated_repos: List[str] = []

    for repo in repos:
        ok, has_changes, reason = _repo_has_remote_changes(repo)
        if not ok:
            return {
                "status": "error",
                "changes_applied": False,
                "updated_repos": updated_repos,
                "messages": [reason],
            }

        if has_changes:
            ok, pull_output = _pull_repo(repo)
            if not ok:
                return {
                    "status": "error",
                    "changes_applied": False,
                    "updated_repos": updated_repos,
                    "messages": [f"Error al hacer pull en {repo.name}: {pull_output}"],
                }
            updated_repos.append(repo.name)
            messages.append(f"Repositorio actualizado: {repo.name}")

    if not updated_repos:
        return {
            "status": "ok",
            "changes_applied": False,
            "updated_repos": [],
            "messages": ["No hay cambios remotos en los repositorios"],
        }

    ok, detail = _run_generation_scripts(workspace_dir)
    if not ok:
        return {
            "status": "error",
            "changes_applied": False,
            "updated_repos": updated_repos,
            "messages": [detail],
        }
    messages.append(detail)

    ok, detail = _copy_generated_files(workspace_dir)
    if not ok:
        return {
            "status": "error",
            "changes_applied": False,
            "updated_repos": updated_repos,
            "messages": [detail],
        }
    messages.append(detail)

    ok, detail = _run_image_pipeline(workspace_dir)
    if not ok:
        return {
            "status": "error",
            "changes_applied": False,
            "updated_repos": updated_repos,
            "messages": [detail],
        }
    messages.append(detail)

    return {
        "status": "ok",
        "changes_applied": True,
        "updated_repos": updated_repos,
        "messages": messages,
    }


def check_soft_update(base_dir: Path) -> Dict[str, object]:
    """Comprueba si hay cambios remotos sin aplicar pulls ni regenerar archivos."""
    workspace_dir = base_dir.parent
    repos = [
        workspace_dir / "pipeline" / "marvelsdb-json-data",
        workspace_dir / "freakmod",
    ]

    repos_with_changes: List[str] = []
    messages: List[str] = []

    for repo in repos:
        ok, has_changes, reason = _repo_has_remote_changes(repo)
        if not ok:
            return {
                "status": "error",
                "changes_detected": False,
                "repos_with_changes": repos_with_changes,
                "messages": [reason],
            }

        if has_changes:
            repos_with_changes.append(repo.name)
            messages.append(f"Hay cambios remotos en: {repo.name}")
        else:
            messages.append(f"Sin cambios remotos en: {repo.name}")

    return {
        "status": "ok",
        "changes_detected": bool(repos_with_changes),
        "repos_with_changes": repos_with_changes,
        "messages": messages,
    }
