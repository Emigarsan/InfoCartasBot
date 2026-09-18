import os
import queue
import subprocess
import sys
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


CONFIG_FILES_TO_COPY = ["factions.json", "sets.json", "types.json", "packs.json"]
BACKUP_KEEP_DEFAULT = 4
FREAKMOD_CONFIG_RELATIVE_DIR = Path("translations") / "es"

# Si un paso en streaming (generacion de imagenes) no produce ninguna linea de
# log en este tiempo, se considera atascado y se aborta en vez de esperar al
# timeout total (que puede ser de horas).
DEFAULT_IDLE_TIMEOUT = 180
# Fragmentos de linea que se reenvian como evento de progreso (para no saturar
# el mensaje de Telegram con una edicion por cada imagen individual).
PROGRESS_LINE_MARKERS = [
    "Total imagenes en carpeta",
    "Progreso:",
    "Flood control",
    "sigo esperando",
    "Atascada:",
    "Error subiendo",
    "Hecho en",
    "Iniciando generacion",
    "Cartas pendientes",
    "Finalizado:",
]

# Repos externos que el pipeline necesita como siblings de bot/ (ver ARCHITECTURE.md).
# La URL de clonado se puede sobreescribir con la variable de entorno indicada; si el
# repo no existe todavia en el servidor, el soft updater lo clona automaticamente ahi.
REPO_DEFINITIONS = [
    {
        "relative_path": Path("pipeline") / "marvelsdb-json-data",
        "url_env": "MARVELSDB_JSON_DATA_REPO_URL",
        "default_url": "https://github.com/zzorba/marvelsdb-json-data.git",
        "label": "marvelsdb-json-data",
        "preferred_remote": "upstream",
        "path_filter": None,
    },
    {
        "relative_path": Path("freakmod"),
        "url_env": "FREAKMOD_REPO_URL",
        "default_url": "https://github.com/Freakmod/marvelsdb-json-data.git",
        "label": "freakmod/translations/es",
        "preferred_remote": "origin",
        "path_filter": FREAKMOD_CONFIG_RELATIVE_DIR,
    },
]


def _repo_definitions(workspace_dir: Path) -> List[Dict[str, object]]:
    resolved = []
    for d in REPO_DEFINITIONS:
        resolved.append({
            **d,
            "repo": workspace_dir / d["relative_path"],
            "url": os.getenv(d["url_env"], d["default_url"]),
        })
    return resolved


def _write_step_log(log_file: Path, content: str) -> None:
    log_file.parent.mkdir(parents=True, exist_ok=True)
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(content)
        if not content.endswith("\n"):
            f.write("\n")


def _format_command_output(command: List[str], cwd: Optional[Path], ok: bool, output: str) -> str:
    status = "OK" if ok else "ERROR"
    cwd_text = str(cwd) if cwd else "(actual)"
    header = f"[{datetime.now().isoformat()}] [{status}] {' '.join(command)}\nCWD: {cwd_text}\n"
    if output:
        return header + "Salida:\n" + output + "\n" + ("-" * 60) + "\n"
    return header + "Sin salida.\n" + ("-" * 60) + "\n"


def _emit_progress(
    progress_log: List[Dict[str, object]],
    progress_callback: Optional[Callable[[Dict[str, object]], None]],
    percent: int,
    step: str,
    detail: str = "",
) -> None:
    event = {
        "percent": max(0, min(100, int(percent))),
        "step": step,
        "detail": detail,
    }
    progress_log.append(event)
    if callable(progress_callback):
        try:
            progress_callback(event)
        except Exception:
            pass


def _is_dubious_ownership_error(message: str) -> bool:
    return "detected dubious ownership" in (message or "").lower()


def _is_unmerged_files_error(message: str) -> bool:
    if not message:
        return False
    text = message.lower()
    checks = [
        "pulling is not possible because you have unmerged files",
        "unmerged files",
        "unresolved conflict",
        "please resolve the conflicts",
        "exiting because of an unresolved conflict",
    ]
    return any(s in text for s in checks)


def _force_reset_to_remote(repo_dir: Path, remote: str, branch: str) -> Tuple[bool, str]:
    outputs: List[str] = []

    # Intenta abortar cualquier operacion en curso
    ok, out = _run_command(["git", "rebase", "--abort"], cwd=repo_dir)
    outputs.append(_format_command_output(["git", "rebase", "--abort"], repo_dir, ok, out))
    ok2, out2 = _run_command(["git", "merge", "--abort"], cwd=repo_dir)
    outputs.append(_format_command_output(["git", "merge", "--abort"], repo_dir, ok2, out2))

    # Fetch y hard-reset a la rama remota
    ok_fetch, out_fetch = _run_command(["git", "fetch", remote], cwd=repo_dir, timeout=300)
    outputs.append(_format_command_output(["git", "fetch", remote], repo_dir, ok_fetch, out_fetch))
    ref = f"{remote}/{branch}"
    ok_reset, out_reset = _run_command(["git", "reset", "--hard", ref], cwd=repo_dir, timeout=300)
    outputs.append(_format_command_output(["git", "reset", "--hard", ref], repo_dir, ok_reset, out_reset))

    ok_clean, out_clean = _run_command(["git", "clean", "-fd"], cwd=repo_dir)
    outputs.append(_format_command_output(["git", "clean", "-fd"], repo_dir, ok_clean, out_clean))

    overall_ok = ok_fetch and ok_reset
    return overall_ok, "\n".join(outputs)


def _add_safe_directory(repo_dir: Path) -> Tuple[bool, str]:
    return _run_command([
        "git",
        "config",
        "--global",
        "--add",
        "safe.directory",
        str(repo_dir),
    ])


def _run_git_command_with_safe_directory(command: List[str], repo_dir: Path, timeout: int = 300) -> Tuple[bool, str]:
    ok, output = _run_command(command, cwd=repo_dir, timeout=timeout)
    if ok:
        return True, output

    if not _is_dubious_ownership_error(output):
        return False, output

    cfg_ok, cfg_output = _add_safe_directory(repo_dir)
    if not cfg_ok:
        return False, (
            f"Git detecto repositorio inseguro en {repo_dir} y no se pudo configurar safe.directory: {cfg_output}"
        )

    retry_ok, retry_output = _run_command(command, cwd=repo_dir, timeout=timeout)
    if retry_ok:
        return True, retry_output

    return False, retry_output


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
        output = "\n".join(part for part in [(result.stdout or "").strip(), (result.stderr or "").strip()] if part).strip()
        return True, output
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        stdout = (exc.stdout or "").strip()
        message_parts = [f"returncode={exc.returncode}"]
        if stdout:
            message_parts.append(f"STDOUT:\n{stdout}")
        if stderr:
            message_parts.append(f"STDERR:\n{stderr}")
        message = "\n\n".join(message_parts) if (stdout or stderr) else str(exc)
        return False, message
    except subprocess.TimeoutExpired as exc:
        return False, f"Timeout tras {timeout}s ejecutando: {' '.join(command)}\n{exc}"


def _run_command_streaming(
    command: List[str],
    cwd: Optional[Path],
    log_file: Path,
    timeout: int = 300,
    idle_timeout: int = DEFAULT_IDLE_TIMEOUT,
    on_line: Optional[Callable[[str], None]] = None,
) -> Tuple[bool, str]:
    """Ejecuta un comando escribiendo cada linea de salida al log en el momento
    en que se produce (en vez de esperar a que el proceso termine), y aborta si
    pasa demasiado tiempo sin ninguna linea nueva (proceso realmente atascado)
    o si supera el timeout total."""
    log_file.parent.mkdir(parents=True, exist_ok=True)

    proc = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )

    line_queue: "queue.Queue[Optional[str]]" = queue.Queue()

    def _reader() -> None:
        try:
            if proc.stdout is not None:
                for line in proc.stdout:
                    line_queue.put(line)
        finally:
            line_queue.put(None)

    reader_thread = threading.Thread(target=_reader, daemon=True)
    reader_thread.start()

    start = time.monotonic()
    last_activity = start
    collected: List[str] = []
    abort_reason: Optional[str] = None

    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{datetime.now().isoformat()}] Ejecutando: {' '.join(command)} (cwd={cwd})\n")
        f.flush()
        while True:
            elapsed_total = time.monotonic() - start
            if elapsed_total >= timeout:
                abort_reason = f"timeout total de {timeout}s"
                break

            idle_for = time.monotonic() - last_activity
            if idle_for >= idle_timeout:
                abort_reason = f"sin actividad durante {idle_timeout}s"
                break

            wait_for = max(0.2, min(idle_timeout - idle_for, timeout - elapsed_total, 2.0))
            try:
                line = line_queue.get(timeout=wait_for)
            except queue.Empty:
                continue

            if line is None:
                break

            last_activity = time.monotonic()
            f.write(line)
            f.flush()
            collected.append(line)
            if callable(on_line):
                try:
                    on_line(line.rstrip("\n"))
                except Exception:
                    pass

    if abort_reason:
        proc.kill()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            pass
        message = f"Proceso abortado ({abort_reason}) tras {time.monotonic() - start:.0f}s"
        _write_step_log(log_file, message)
        cola = "".join(collected[-40:])
        return False, f"{message}\nUltimas lineas:\n{cola}" if cola else message

    returncode = proc.wait()
    output = "".join(collected)
    if returncode != 0:
        return False, f"returncode={returncode}\n{output[-4000:]}"
    return True, output


def _clone_repo(repo_dir: Path, url: str) -> Tuple[bool, str]:
    repo_dir.parent.mkdir(parents=True, exist_ok=True)
    return _run_command(["git", "clone", url, str(repo_dir)], cwd=repo_dir.parent, timeout=600)


def _current_branch(repo_dir: Path) -> str:
    ok, branch = _run_git_command_with_safe_directory(["git", "rev-parse", "--abbrev-ref", "HEAD"], repo_dir)
    if ok and branch and branch != "HEAD":
        return branch
    return "main"


def _remote_exists(repo_dir: Path, remote: str) -> bool:
    ok, _ = _run_git_command_with_safe_directory(["git", "remote", "get-url", remote], repo_dir)
    return ok


def _resolve_remote(repo_dir: Path, preferred_remote: str) -> str:
    candidates: List[str] = []
    for name in [preferred_remote, "origin", "upstream"]:
        if name and name not in candidates:
            candidates.append(name)

    for remote in candidates:
        if _remote_exists(repo_dir, remote):
            return remote

    return preferred_remote


def _remote_has_branch(repo_dir: Path, remote: str, branch: str) -> bool:
    ok, output = _run_git_command_with_safe_directory(["git", "ls-remote", "--heads", remote, branch], repo_dir)
    return ok and bool(output.strip())


def _resolve_remote_branch(repo_dir: Path, remote: str) -> str:
    candidates: List[str] = []
    local_branch = _current_branch(repo_dir)
    candidates.append(local_branch)
    for fallback in ["main", "master"]:
        if fallback not in candidates:
            candidates.append(fallback)

    for branch in candidates:
        if _remote_has_branch(repo_dir, remote, branch):
            return branch

    return local_branch


def _repo_has_remote_changes(repo_dir: Path, url: str, preferred_remote: str = "origin") -> Tuple[bool, bool, str]:
    if not (repo_dir / ".git").exists():
        if repo_dir.exists() and any(repo_dir.iterdir()):
            return False, False, (
                f"{repo_dir} existe pero no es un repositorio git valido (sin .git) y no esta vacio. "
                "Revisalo/borralo manualmente antes de reintentar la actualizacion."
            )
        ok, output = _clone_repo(repo_dir, url)
        if not ok:
            return False, False, f"No se pudo clonar {repo_dir.name} desde {url}: {output}"
        # Repo recien clonado: ya esta al dia, se trata como "hay cambios" para
        # disparar la generacion inicial del pipeline.
        return True, True, ""

    remote = _resolve_remote(repo_dir, preferred_remote)

    ok, output = _run_git_command_with_safe_directory(["git", "status", "--porcelain"], repo_dir)
    if not ok:
        return False, False, f"No se pudo comprobar estado git en {repo_dir.name}: {output}"
    has_local_changes = bool(output.strip())
    warning = ""
    if has_local_changes:
        warning = (
            f"Repositorio con cambios locales en {repo_dir.name}. "
            "Se permitira check y se intentara pull con autostash en /actualizar."
        )

    ok, output = _run_git_command_with_safe_directory(["git", "fetch", remote], repo_dir)
    if not ok:
        return False, False, f"No se pudo hacer fetch en {repo_dir.name} ({remote}): {output}"

    branch = _resolve_remote_branch(repo_dir, remote)
    ok, output = _run_git_command_with_safe_directory(["git", "rev-list", "--count", f"HEAD..{remote}/{branch}"], repo_dir)
    if not ok:
        return False, False, f"No se pudo comparar ramas en {repo_dir.name} ({remote}/{branch}): {output}"

    try:
        has_changes = int((output or "0").strip() or "0") > 0
    except ValueError:
        has_changes = bool(output.strip())

    return True, has_changes, warning


def _repo_path_has_remote_changes(
    repo_dir: Path,
    url: str,
    relative_path: Path,
    preferred_remote: str = "origin",
) -> Tuple[bool, bool, str]:
    ok, has_changes, warning = _repo_has_remote_changes(repo_dir, url, preferred_remote=preferred_remote)
    if not ok:
        return ok, has_changes, warning
    if not has_changes:
        return True, False, warning

    remote = _resolve_remote(repo_dir, preferred_remote)
    branch = _resolve_remote_branch(repo_dir, remote)
    ok, output = _run_git_command_with_safe_directory(
        ["git", "diff", "--name-only", f"HEAD..{remote}/{branch}", "--", str(relative_path).replace("\\", "/")],
        repo_dir,
    )
    if not ok:
        return False, False, f"No se pudo revisar cambios en {repo_dir.name}/{relative_path}: {output}"

    return True, bool(output.strip()), warning


def _pull_repo(repo_dir: Path, preferred_remote: str = "origin") -> Tuple[bool, str]:
    remote = _resolve_remote(repo_dir, preferred_remote)
    branch = _resolve_remote_branch(repo_dir, remote)
    ok, output = _run_git_command_with_safe_directory(["git", "status", "--porcelain"], repo_dir)
    if not ok:
        return False, f"No se pudo comprobar estado git en {repo_dir.name}: {output}"

    has_local_changes = bool(output.strip())
    # Intenta el pull. Si falla por unmerged files, se aborta/limpia y se hace hard-reset al remoto.
    if has_local_changes:
        ok, output = _run_git_command_with_safe_directory(
            ["git", "pull", "--rebase", "--autostash", remote, branch],
            repo_dir,
            timeout=600,
        )
    else:
        ok, output = _run_git_command_with_safe_directory(["git", "pull", remote, branch], repo_dir, timeout=600)

    if ok:
        return True, output

    if _is_unmerged_files_error(output):
        reset_ok, reset_output = _force_reset_to_remote(repo_dir, remote, branch)
        if reset_ok:
            return True, f"Pull fallido por conflictos. Se forzo reset a {remote}/{branch}:\n{reset_output}"
        return False, f"Pull fallido por conflictos y no se pudo forzar reset:\n{reset_output}"

    return False, output


def _run_generation_scripts(workspace_dir: Path, log_dir: Path) -> Tuple[bool, str]:
    pipeline_dir = workspace_dir / "pipeline"
    log_json = log_dir / "03_json_traducido.log"
    log_merge = log_dir / "04_merge_json.log"

    ok, output = _run_command([sys.executable, "json_traducido.py"], cwd=pipeline_dir, timeout=1800)
    _write_step_log(log_json, _format_command_output([sys.executable, "json_traducido.py"], pipeline_dir, ok, output))
    if not ok:
        return False, f"Error en json_traducido.py: {output}"

    ok, output = _run_command([sys.executable, "merge_json.py"], cwd=pipeline_dir, timeout=1800)
    _write_step_log(log_merge, _format_command_output([sys.executable, "merge_json.py"], pipeline_dir, ok, output))
    if not ok:
        return False, f"Error en merge_json.py: {output}"

    return True, "Scripts de generación ejecutados correctamente"


def _copy_generated_files(workspace_dir: Path, log_dir: Path) -> Tuple[bool, str]:
    pipeline_dir = workspace_dir / "pipeline"
    data_dir = workspace_dir / "bot" / "data"
    copy_log = log_dir / "05_copiado_archivos.log"
    copied_files: List[str] = []

    source_json = pipeline_dir / "marvelcdb_spanish.json"
    target_json = data_dir / "marvelcdb_spanish.json"
    if not source_json.exists():
        return False, f"No existe archivo generado: {source_json}"
    shutil.copy2(source_json, target_json)
    copied_files.append(f"{source_json} -> {target_json}")

    preferred_config_dir = workspace_dir / "freakmod" / FREAKMOD_CONFIG_RELATIVE_DIR
    fallback_config_dir = pipeline_dir / "marvelsdb-json-data" / "translations" / "es"

    if preferred_config_dir.exists():
        source_config_dir = preferred_config_dir
    elif fallback_config_dir.exists():
        source_config_dir = fallback_config_dir
    else:
        return False, (
            "No existe directorio de configuración en Freakmod ni en marvelsdb-json-data: "
            f"{preferred_config_dir} | {fallback_config_dir}"
        )

    for file_name in CONFIG_FILES_TO_COPY:
        source = source_config_dir / file_name
        target = data_dir / file_name
        if not source.exists():
            return False, f"Falta archivo de configuración: {source}"
        shutil.copy2(source, target)
        copied_files.append(f"{source} -> {target}")

    _write_step_log(copy_log, f"Origen de configuraciones: {source_config_dir}\n" + "\n".join(copied_files) + "\n")

    return True, "JSON y configuraciones copiadas a bot/data"


def _make_image_step_line_handler(
    progress_log: List[Dict[str, object]],
    progress_callback: Optional[Callable[[Dict[str, object]], None]],
    percent: int,
    step: str,
) -> Callable[[str], None]:
    def _on_line(line: str) -> None:
        if not line.strip():
            return
        if any(marker.lower() in line.lower() for marker in PROGRESS_LINE_MARKERS):
            _emit_progress(progress_log, progress_callback, percent, step, line.strip())

    return _on_line


def _run_image_pipeline(
    workspace_dir: Path,
    log_dir: Path,
    progress_log: Optional[List[Dict[str, object]]] = None,
    progress_callback: Optional[Callable[[Dict[str, object]], None]] = None,
) -> Tuple[bool, str]:
    """Genera imagenes con marca de agua y las sube a Telegram.

    Ejecuta ambos scripts en modo streaming: cada linea se escribe al log en
    el momento en que se produce (no al terminar el proceso) y si un script se
    queda sin imprimir nada durante DEFAULT_IDLE_TIMEOUT segundos, se aborta en
    vez de esperar en silencio hasta el timeout total (que puede ser de horas).
    """
    data_dir = workspace_dir / "bot" / "data"
    log_marca = log_dir / "06_marca_agua.log"
    log_upload = log_dir / "07_telegram_imagenes.log"
    progress_log = progress_log if progress_log is not None else []

    ok, output = _run_command_streaming(
        [sys.executable, "marca_agua_v2.py"],
        cwd=data_dir,
        log_file=log_marca,
        timeout=3600,
        on_line=_make_image_step_line_handler(progress_log, progress_callback, 90, "imagenes: marca de agua"),
    )
    if not ok:
        return False, f"Error en marca_agua_v2.py: {output}"

    ok, output = _run_command_streaming(
        [sys.executable, "telegram_imagenes.py"],
        cwd=data_dir,
        log_file=log_upload,
        timeout=7200,
        on_line=_make_image_step_line_handler(progress_log, progress_callback, 95, "imagenes: subida a Telegram"),
    )
    if not ok:
        return False, f"Error en telegram_imagenes.py: {output}"

    return True, "Imagenes con marca de agua generadas y subidas a Telegram"


def _create_backups(workspace_dir: Path, log_dir: Path) -> Tuple[bool, str]:
    """Crea una copia completa por carpeta timestamp y aplica retencion por carpetas."""
    data_dir = workspace_dir / "bot" / "data"
    backup_dir = workspace_dir / "bot" / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)

    keep = BACKUP_KEEP_DEFAULT
    try:
        keep = int(os.getenv("BOT_UPDATE_BACKUP_KEEP", str(BACKUP_KEEP_DEFAULT)))
    except ValueError:
        keep = BACKUP_KEEP_DEFAULT
    keep = max(1, keep)

    files_to_backup = [
        data_dir / "marvelcdb_spanish.json",
        data_dir / "IC_file_id_cache.json",
        *[data_dir / file_name for file_name in CONFIG_FILES_TO_COPY],
    ]

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    snapshot_dir = backup_dir / timestamp
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    copied = 0
    for source in files_to_backup:
        if not source.exists():
            continue
        destination = snapshot_dir / source.name
        shutil.copy2(source, destination)
        copied += 1

    backup_snapshots = [path for path in backup_dir.glob("*") if path.is_dir()]
    backup_snapshots.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    removed_snapshots: List[str] = []
    for old_snapshot in backup_snapshots[keep:]:
        try:
            shutil.rmtree(old_snapshot)
            removed_snapshots.append(str(old_snapshot))
        except OSError:
            pass

    kept_now = min(keep, len(backup_snapshots))
    backup_log = log_dir / "02_backup.log"
    log_lines = [
        f"Snapshot creado: {snapshot_dir}",
        f"Archivos copiados: {copied}",
        f"Retencion carpetas: {keep}",
        f"Carpetas actuales: {kept_now}",
    ]
    if removed_snapshots:
        log_lines.append("Carpetas eliminadas por retencion:")
        log_lines.extend(removed_snapshots)
    _write_step_log(backup_log, "\n".join(log_lines) + "\n")
    return True, (
        f"Backup completo guardado en {snapshot_dir} "
        f"(archivos copiados: {copied}, retencion carpetas: {keep}, carpetas actuales: {kept_now})"
    )


def run_soft_update(
    base_dir: Path,
    force: bool = False,
    progress_callback: Optional[Callable[[Dict[str, object]], None]] = None,
) -> Dict[str, object]:
    """
    Actualización en caliente sin reiniciar servicios.

    Usa base_dir del bot (bot/) para resolver rutas al workspace raíz.
    """
    workspace_dir = base_dir.parent
    logs_root = base_dir / "logs"
    run_ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_log_dir = logs_root / f"update_{run_ts}"
    run_log_dir.mkdir(parents=True, exist_ok=True)

    repos = _repo_definitions(workspace_dir)

    messages: List[str] = []
    updated_repos: List[str] = []
    progress_log: List[Dict[str, object]] = []

    _emit_progress(progress_log, progress_callback, 5, "inicio", "Preparando comprobacion de repositorios")
    _write_step_log(run_log_dir / "01_inicio.log", f"Inicio de actualizacion. force={force}\n")

    total_repos = len(repos)
    for index, repo_item in enumerate(repos, start=1):
        repo = repo_item["repo"]
        url = repo_item["url"]
        path_filter = repo_item["path_filter"]
        repo_label = repo_item["label"]
        preferred_remote = repo_item["preferred_remote"]
        percent = 10 + int((index - 1) * (25 / max(1, total_repos)))
        _emit_progress(progress_log, progress_callback, percent, "repos", f"Comprobando {repo_label} ({index}/{total_repos})")

        if path_filter is None:
            ok, has_changes, reason = _repo_has_remote_changes(repo, url, preferred_remote=preferred_remote)
        else:
            ok, has_changes, reason = _repo_path_has_remote_changes(repo, url, path_filter, preferred_remote=preferred_remote)

        if not ok:
            _write_step_log(run_log_dir / "01_repos.log", f"Error en {repo_label}: {reason}\n")
            return {
                "status": "error",
                "changes_applied": False,
                "updated_repos": updated_repos,
                "messages": [reason],
                "progress": progress_log,
                "logs_dir": str(run_log_dir),
            }

        if reason:
            messages.append(reason)
            _write_step_log(run_log_dir / "01_repos.log", reason + "\n")

        if has_changes or force:
            remote = _resolve_remote(repo, preferred_remote)
            branch = _resolve_remote_branch(repo, remote)
            if force and not has_changes:
                messages.append(f"Modo forzado: se ejecuta pull en {repo_label} aunque no se detecten cambios remotos.")
            _emit_progress(progress_log, progress_callback, percent + 10, "repos", f"Aplicando pull en {repo_label}")
            ok, pull_output = _pull_repo(repo, preferred_remote=preferred_remote)
            _write_step_log(
                run_log_dir / "01_repos.log",
                _format_command_output(["git", "pull", remote, branch], repo, ok, pull_output),
            )
            if not ok:
                return {
                    "status": "error",
                    "changes_applied": False,
                    "updated_repos": updated_repos,
                    "messages": [f"Error al hacer pull en {repo_label}: {pull_output}"],
                    "progress": progress_log,
                    "logs_dir": str(run_log_dir),
                }
            updated_repos.append(repo_label)
            messages.append(f"Repositorio actualizado: {repo_label}")

    if not updated_repos and not force:
        return {
            "status": "ok",
            "changes_applied": False,
            "updated_repos": [],
            "messages": ["No hay cambios remotos en los repositorios"],
            "progress": progress_log,
            "logs_dir": str(run_log_dir),
        }

    if force and not updated_repos:
        messages.append("Modo forzado activo: se ejecuta pipeline sin cambios remotos.")

    _emit_progress(progress_log, progress_callback, 45, "backup", "Creando backups previos")
    ok, detail = _create_backups(workspace_dir, run_log_dir)
    if not ok:
        return {
            "status": "error",
            "changes_applied": False,
            "updated_repos": updated_repos,
            "messages": [detail],
            "progress": progress_log,
            "logs_dir": str(run_log_dir),
        }
    messages.append(detail)

    _emit_progress(progress_log, progress_callback, 60, "generacion", "Ejecutando json_traducido y merge_json")
    ok, detail = _run_generation_scripts(workspace_dir, run_log_dir)
    if not ok:
        return {
            "status": "error",
            "changes_applied": False,
            "updated_repos": updated_repos,
            "messages": [detail],
            "progress": progress_log,
            "logs_dir": str(run_log_dir),
        }
    messages.append(detail)

    _emit_progress(progress_log, progress_callback, 75, "copiado", "Copiando JSON y ficheros de configuracion")
    ok, detail = _copy_generated_files(workspace_dir, run_log_dir)
    if not ok:
        return {
            "status": "error",
            "changes_applied": False,
            "updated_repos": updated_repos,
            "messages": [detail],
            "progress": progress_log,
            "logs_dir": str(run_log_dir),
        }
    messages.append(detail)

    _emit_progress(progress_log, progress_callback, 90, "imagenes", "Generando imagenes y subiendo a Telegram")
    ok, detail = _run_image_pipeline(workspace_dir, run_log_dir, progress_log, progress_callback)
    if not ok:
        return {
            "status": "error",
            "changes_applied": False,
            "updated_repos": updated_repos,
            "messages": [detail],
            "progress": progress_log,
            "logs_dir": str(run_log_dir),
        }
    messages.append(detail)

    _emit_progress(progress_log, progress_callback, 98, "recarga", "Pipeline finalizado, listo para recarga en memoria")

    return {
        "status": "ok",
        "changes_applied": True,
        "updated_repos": updated_repos,
        "messages": messages,
        "progress": progress_log,
        "logs_dir": str(run_log_dir),
    }


def check_soft_update(base_dir: Path) -> Dict[str, object]:
    """Comprueba si hay cambios remotos sin aplicar pulls ni regenerar archivos."""
    workspace_dir = base_dir.parent
    repos = _repo_definitions(workspace_dir)

    repos_with_changes: List[str] = []
    messages: List[str] = []

    for repo_item in repos:
        repo = repo_item["repo"]
        url = repo_item["url"]
        path_filter = repo_item["path_filter"]
        repo_label = repo_item["label"]
        preferred_remote = repo_item["preferred_remote"]

        if path_filter is None:
            ok, has_changes, reason = _repo_has_remote_changes(repo, url, preferred_remote=preferred_remote)
        else:
            ok, has_changes, reason = _repo_path_has_remote_changes(repo, url, path_filter, preferred_remote=preferred_remote)

        if not ok:
            return {
                "status": "error",
                "changes_detected": False,
                "repos_with_changes": repos_with_changes,
                "messages": [reason],
            }

        if reason:
            messages.append(reason)

        if has_changes:
            repos_with_changes.append(repo_label)
            messages.append(f"Hay cambios remotos en: {repo_label}")
        else:
            messages.append(f"Sin cambios remotos en: {repo_label}")

    return {
        "status": "ok",
        "changes_detected": bool(repos_with_changes),
        "repos_with_changes": repos_with_changes,
        "messages": messages,
    }
