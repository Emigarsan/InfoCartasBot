import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path, PurePosixPath
from typing import Dict, List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

CONFIG_FILES_TO_COPY = ["factions.json", "sets.json", "types.json", "packs.json"]
DEFAULT_SYNC_FILES = [
    "bot/data/marvelcdb_spanish.json",
    "bot/data/factions.json",
    "bot/data/sets.json",
    "bot/data/types.json",
    "bot/data/packs.json",
    "bot/data/IC_file_id_cache.json",
    "bot/data/failed_cards.json",
]


def _run_command(
    command: List[str],
    cwd: Optional[Path] = None,
    timeout: int = 300,
    env: Optional[Dict[str, str]] = None,
    step_name: Optional[str] = None,
) -> Tuple[bool, str]:
    label = step_name or Path(command[0]).name
    command_text = " ".join(command)
    start = time.perf_counter()
    print(f"[STEP] Iniciando {label}")
    print(f"[STEP] Comando: {command_text}")
    if cwd:
        print(f"[STEP] Directorio: {cwd}")

    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            env=env,
            bufsize=1,
        )
        output_lines: List[str] = []
        deadline = time.perf_counter() + timeout if timeout else None

        assert process.stdout is not None
        while True:
            if deadline is not None and time.perf_counter() > deadline:
                process.kill()
                remaining = "\n".join(output_lines[-20:]).strip()
                return False, f"Timeout tras {timeout}s en {label}. Ultimas lineas:\n{remaining}"

            line = process.stdout.readline()
            if line:
                clean = line.rstrip()
                output_lines.append(clean)
                print(f"[{label}] {clean}")
                continue

            if process.poll() is not None:
                break

            time.sleep(0.1)

        return_code = process.wait(timeout=5)
        elapsed = time.perf_counter() - start
        if return_code == 0:
            print(f"[STEP] Finalizado {label} en {elapsed:.1f}s")
            return True, "\n".join(output_lines).strip()

        message = "\n".join(output_lines[-20:]).strip()
        print(f"[STEP] Fallo {label} en {elapsed:.1f}s con codigo {return_code}")
        return False, message or f"Codigo de salida {return_code}"
    except Exception as exc:
        elapsed = time.perf_counter() - start
        print(f"[STEP] Excepcion en {label} tras {elapsed:.1f}s: {exc}")
        return False, str(exc)


def _sync_directory_contents(source_dir: Path, target_dir: Path) -> Tuple[bool, int, str]:
    if not source_dir.exists() or not source_dir.is_dir():
        return False, 0, f"No existe el directorio origen: {source_dir}"

    target_dir.mkdir(parents=True, exist_ok=True)
    copied_files = 0
    scanned_files = 0
    started = time.perf_counter()

    print(f"[SYNC] Copiando contenido de {source_dir} -> {target_dir}")

    for root, dirs, files in os.walk(source_dir):
        dirs[:] = [d for d in dirs if d != ".git"]
        rel_root = Path(root).relative_to(source_dir)
        target_root = target_dir / rel_root
        target_root.mkdir(parents=True, exist_ok=True)

        for file_name in files:
            scanned_files += 1
            source_file = Path(root) / file_name
            target_file = target_root / file_name

            source_hash = _sha256_file(source_file)
            target_hash = _sha256_file(target_file)
            if source_hash != target_hash:
                shutil.copy2(source_file, target_file)
                copied_files += 1
                if copied_files <= 10 or copied_files % 50 == 0:
                    print(f"[SYNC] Copiado {copied_files}: {source_file.relative_to(source_dir)}")

    elapsed = time.perf_counter() - started
    return True, copied_files, (
        f"Sincronizado {source_dir} -> {target_dir} en {elapsed:.1f}s "
        f"(archivos revisados: {scanned_files}, archivos copiados: {copied_files})"
    )


def _sha256_file(path: Path) -> Optional[str]:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _snapshot_files(workspace_dir: Path, rel_paths: List[str]) -> Dict[str, Optional[str]]:
    snapshot: Dict[str, Optional[str]] = {}
    for rel in rel_paths:
        local_path = workspace_dir / rel
        snapshot[rel] = _sha256_file(local_path)
    return snapshot


def _run_generation_scripts(workspace_dir: Path, marvel_repo_dir: Path) -> Tuple[bool, str]:
    pipeline_dir = workspace_dir / "pipeline"
    env = os.environ.copy()
    env["MARVELCDB_REPO_DIR"] = str(marvel_repo_dir)
    env["TRANSLATION_REPO_DIR"] = str(workspace_dir / "freakmod")

    print(f"[INFO] json_traducido.py usara MARVELCDB_REPO_DIR={env['MARVELCDB_REPO_DIR']}")
    print(f"[INFO] json_traducido.py usara TRANSLATION_REPO_DIR={env['TRANSLATION_REPO_DIR']}")

    ok, output = _run_command(
        [sys.executable, "json_traducido.py"],
        cwd=pipeline_dir,
        timeout=1800,
        env=env,
        step_name="json_traducido.py",
    )
    if not ok:
        return False, f"Error en json_traducido.py: {output}"

    ok, output = _run_command(
        [sys.executable, "merge_json.py"],
        cwd=pipeline_dir,
        timeout=1800,
        env=env,
        step_name="merge_json.py",
    )
    if not ok:
        return False, f"Error en merge_json.py: {output}"

    return True, "Scripts de generación ejecutados correctamente"


def _copy_generated_files(workspace_dir: Path, marvel_repo_dir: Path) -> Tuple[bool, str]:
    pipeline_dir = workspace_dir / "pipeline"
    data_dir = workspace_dir / "bot" / "data"

    source_json = pipeline_dir / "marvelcdb_spanish.json"
    target_json = data_dir / "marvelcdb_spanish.json"
    if not source_json.exists():
        return False, f"No existe archivo generado: {source_json}"
    shutil.copy2(source_json, target_json)

    source_config_dir = marvel_repo_dir / "translations" / "es"
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
    data_dir = workspace_dir / "bot" / "data"

    ok, output = _run_command(
        [sys.executable, "marca_agua_v2.py"],
        cwd=data_dir,
        timeout=3600,
        step_name="marca_agua_v2.py",
    )
    if not ok:
        return False, f"Error en marca_agua_v2.py: {output}"

    ok, output = _run_command(
        [sys.executable, "telegram_imagenes.py"],
        cwd=data_dir,
        timeout=7200,
        step_name="telegram_imagenes.py",
    )
    if not ok:
        return False, f"Error en telegram_imagenes.py: {output}"

    return True, "Imagenes con marca de agua generadas y subidas a Telegram"


def _ensure_remote_dir(sftp, remote_dir: str) -> None:
    current = PurePosixPath("/")
    for part in PurePosixPath(remote_dir).parts:
        if part == "/":
            continue
        current = current / part
        try:
            sftp.stat(str(current))
        except FileNotFoundError:
            sftp.mkdir(str(current))


def _upload_sftp_files(
    workspace_dir: Path,
    rel_paths: List[str],
    host: str,
    port: int,
    username: str,
    password: Optional[str],
    key_file: Optional[str],
    key_passphrase: Optional[str],
    remote_base: str,
) -> Tuple[bool, str]:
    try:
        import paramiko
    except ImportError:
        return False, "Falta dependencia 'paramiko'. Instala con: pip install paramiko"

    transport = None
    try:
        transport = paramiko.Transport((host, port))

        if key_file:
            pkey = None
            key_errors: List[str] = []
            for key_cls in [paramiko.RSAKey, paramiko.ECDSAKey, paramiko.Ed25519Key]:
                try:
                    pkey = key_cls.from_private_key_file(key_file, password=key_passphrase)
                    break
                except Exception as exc:
                    key_errors.append(str(exc))
            if pkey is None:
                return False, "No se pudo cargar la clave privada indicada en SFTP_KEY_FILE"
            transport.connect(username=username, pkey=pkey)
        else:
            transport.connect(username=username, password=password)

        sftp = paramiko.SFTPClient.from_transport(transport)
        uploaded_count = 0

        for rel in rel_paths:
            local_path = workspace_dir / rel
            if not local_path.exists():
                continue

            remote_target = PurePosixPath(remote_base) / PurePosixPath(rel.replace("\\", "/"))
            remote_dir = str(remote_target.parent)
            _ensure_remote_dir(sftp, remote_dir)
            sftp.put(str(local_path), str(remote_target))
            uploaded_count += 1
            print(f"[SFTP] Subido: {rel} -> {remote_target}")

        sftp.close()
        return True, f"Subida SFTP completada. Archivos subidos: {uploaded_count}"
    except Exception as exc:
        return False, f"Error durante subida SFTP: {exc}"
    finally:
        if transport is not None:
            transport.close()


def run_local_update_and_sftp(
    workspace_dir: Path,
    marvel_repo_dir: Path,
    freakmod_repo_dir: Path,
    skip_image_pipeline: bool = False,
    force: bool = False,
    no_sftp: bool = False,
    sync_files: Optional[List[str]] = None,
    sftp_host: Optional[str] = None,
    sftp_port: int = 22,
    sftp_username: Optional[str] = None,
    sftp_password: Optional[str] = None,
    sftp_key_file: Optional[str] = None,
    sftp_key_passphrase: Optional[str] = None,
    sftp_remote_base: str = "/",
) -> int:
    marvel_target_dir = workspace_dir / "pipeline" / "marvelsdb-json-data"
    freakmod_target_dir = workspace_dir / "freakmod"

    tracked_files = sync_files or DEFAULT_SYNC_FILES
    before = _snapshot_files(workspace_dir, tracked_files)

    print("[INFO] Iniciando actualización local completa")
    print(f"[INFO] Workspace: {workspace_dir}")
    print(f"[INFO] Origen MarvelCDB: {marvel_repo_dir}")
    print(f"[INFO] Origen Freakmod: {freakmod_repo_dir}")
    print(f"[INFO] Destino MarvelCDB: {marvel_target_dir}")
    print(f"[INFO] Destino Freakmod: {freakmod_target_dir}")

    ok, copied_count, detail = _sync_directory_contents(marvel_repo_dir, marvel_target_dir)
    if not ok:
        print(f"[ERROR] {detail}")
        return 1
    print(f"[OK] {detail}")

    ok, copied_count_2, detail = _sync_directory_contents(freakmod_repo_dir, freakmod_target_dir)
    if not ok:
        print(f"[ERROR] {detail}")
        return 1
    print(f"[OK] {detail}")

    if copied_count == 0 and copied_count_2 == 0 and force:
        print("[INFO] --force activo: se ejecuta pipeline aunque no haya archivos nuevos al copiar.")

    print("[INFO] Paso 1/4: generación de JSON")
    ok, detail = _run_generation_scripts(workspace_dir, marvel_target_dir)
    if not ok:
        print(f"[ERROR] {detail}")
        return 1
    print(f"[OK] {detail}")

    print("[INFO] Paso 2/4: copia de artefactos generados")
    ok, detail = _copy_generated_files(workspace_dir, marvel_target_dir)
    if not ok:
        print(f"[ERROR] {detail}")
        return 1
    print(f"[OK] {detail}")

    print("[INFO] Paso 3/4: pipeline de imagenes y Telegram")
    if not skip_image_pipeline:
        ok, detail = _run_image_pipeline(workspace_dir)
        if not ok:
            print(f"[ERROR] {detail}")
            return 1
        print(f"[OK] {detail}")
    else:
        print("[INFO] Pipeline de imagenes omitido por parametro.")

    after = _snapshot_files(workspace_dir, tracked_files)
    changed = [rel for rel in tracked_files if before.get(rel) != after.get(rel)]

    if not changed:
        print("[INFO] No hubo cambios en los archivos configurados para sincronizar.")
        return 0

    print(f"[INFO] Paso 4/4: subida SFTP de {len(changed)} archivo(s) modificados")
    print("[INFO] Archivos modificados para sincronizar:")
    for rel in changed:
        print(f"  - {rel}")

    if no_sftp:
        print("[INFO] Modo --no-sftp activo. No se suben archivos.")
        return 0

    host = sftp_host or os.getenv("SFTP_HOST")
    username = sftp_username or os.getenv("SFTP_USER")
    password = sftp_password if sftp_password is not None else os.getenv("SFTP_PASSWORD")
    key_file = sftp_key_file or os.getenv("SFTP_KEY_FILE")
    key_passphrase = sftp_key_passphrase if sftp_key_passphrase is not None else os.getenv("SFTP_KEY_PASSPHRASE")
    remote_base = sftp_remote_base or os.getenv("SFTP_REMOTE_BASE", "/")

    if not host or not username:
        print("[ERROR] Faltan credenciales SFTP. Define SFTP_HOST y SFTP_USER (args o variables de entorno).")
        return 1

    if not password and not key_file:
        print("[ERROR] Debes proporcionar SFTP_PASSWORD o SFTP_KEY_FILE.")
        return 1

    ok, detail = _upload_sftp_files(
        workspace_dir=workspace_dir,
        rel_paths=changed,
        host=host,
        port=sftp_port,
        username=username,
        password=password,
        key_file=key_file,
        key_passphrase=key_passphrase,
        remote_base=remote_base,
    )
    if not ok:
        print(f"[ERROR] {detail}")
        return 1

    print(f"[OK] {detail}")
    return 0


def _parse_args() -> argparse.Namespace:
    script_dir = Path(__file__).resolve().parent
    workspace_default = script_dir.parent

    parser = argparse.ArgumentParser(
        description="Ejecuta actualización local completa y sube por SFTP solo archivos cambiados."
    )
    parser.add_argument("--workspace-dir", default=str(workspace_default))
    parser.add_argument(
        "--marvel-repo",
        default=os.getenv("MARVEL_LOCAL_REPO_PATH"),
        help="Ruta origen local de marvelsdb-json-data (se copia a pipeline/marvelsdb-json-data). "
        "Tambien se puede definir con la variable de entorno MARVEL_LOCAL_REPO_PATH.",
    )
    parser.add_argument(
        "--freakmod-repo",
        default=os.getenv("FREAKMOD_LOCAL_REPO_PATH"),
        help="Ruta origen local de freakmod (se copia a freakmod/). "
        "Tambien se puede definir con la variable de entorno FREAKMOD_LOCAL_REPO_PATH.",
    )
    parser.add_argument("--skip-image-pipeline", action="store_true")
    parser.add_argument("--force", action="store_true", help="Ejecuta pipeline aunque no haya cambios remotos.")
    parser.add_argument("--no-sftp", action="store_true", help="Ejecuta todo local sin subida SFTP.")
    parser.add_argument(
        "--sync-file",
        action="append",
        dest="sync_files",
        help="Archivo relativo al workspace a considerar para subida incremental. Repetible.",
    )

    parser.add_argument("--sftp-host")
    parser.add_argument("--sftp-port", type=int, default=int(os.getenv("SFTP_PORT", "22")))
    parser.add_argument("--sftp-user")
    parser.add_argument("--sftp-password")
    parser.add_argument("--sftp-key-file")
    parser.add_argument("--sftp-key-passphrase")
    parser.add_argument("--sftp-remote-base", default=os.getenv("SFTP_REMOTE_BASE", "/"))
    parser.add_argument("--quiet", action="store_true", help="Reduce la salida a mensajes de alto nivel.")

    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    workspace_dir = Path(args.workspace_dir).resolve()

    if not args.marvel_repo or not args.freakmod_repo:
        print(
            "[ERROR] Faltan rutas de repos locales. Define MARVEL_LOCAL_REPO_PATH y "
            "FREAKMOD_LOCAL_REPO_PATH (variables de entorno) o usa --marvel-repo/--freakmod-repo."
        )
        return 1

    if args.quiet:
        print("[INFO] Modo silencioso solicitado, pero los pasos largos seguirán mostrando inicio/fin.")

    return run_local_update_and_sftp(
        workspace_dir=workspace_dir,
        marvel_repo_dir=Path(args.marvel_repo).resolve(),
        freakmod_repo_dir=Path(args.freakmod_repo).resolve(),
        skip_image_pipeline=args.skip_image_pipeline,
        force=args.force,
        no_sftp=args.no_sftp,
        sync_files=args.sync_files,
        sftp_host=args.sftp_host,
        sftp_port=args.sftp_port,
        sftp_username=args.sftp_user,
        sftp_password=args.sftp_password,
        sftp_key_file=args.sftp_key_file,
        sftp_key_passphrase=args.sftp_key_passphrase,
        sftp_remote_base=args.sftp_remote_base,
    )


if __name__ == "__main__":
    raise SystemExit(main())
