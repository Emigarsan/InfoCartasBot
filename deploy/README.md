# Actualización local + SFTP

Este flujo ejecuta en local lo mismo que el soft update:

1. Comprueba cambios remotos en los repos.
2. Hace pull de los repos actualizados.
3. Ejecuta `pipeline/json_traducido.py` + `pipeline/merge_json.py`.
4. Copia JSON y configuraciones a `bot/data`.
5. Ejecuta `bot/data/marca_agua_v2.py` y `bot/data/telegram_imagenes.py`.
6. Sube por SFTP solo archivos que hayan cambiado.

## Dependencias

```bash
pip install -r deploy/requirements.txt
```

## Variables de entorno

Puedes pasarlas por argumentos o por variables de entorno:

- `MARVEL_LOCAL_REPO_PATH` — ruta local del clon de `marvelsdb-json-data` (ver `--marvel-repo`)
- `FREAKMOD_LOCAL_REPO_PATH` — ruta local del clon de `freakmod` (ver `--freakmod-repo`)
- `SFTP_HOST`
- `SFTP_PORT` (opcional, por defecto 22)
- `SFTP_USER`
- `SFTP_PASSWORD` o `SFTP_KEY_FILE`
- `SFTP_KEY_PASSPHRASE` (opcional)
- `SFTP_REMOTE_BASE` (por defecto `/`)

## Ejecución

Desde la raíz del proyecto, con `MARVEL_LOCAL_REPO_PATH` y `FREAKMOD_LOCAL_REPO_PATH` ya definidas:

```bash
python deploy/run_local_sftp_update.py
```

Modo local sin subir:

```bash
python deploy/run_local_sftp_update.py --no-sftp
```

Forzar ejecución aunque no haya cambios remotos:

```bash
python deploy/run_local_sftp_update.py --force
```

O pasando las rutas explícitamente en vez de por variable de entorno:

```bash
python deploy/run_local_sftp_update.py \
  --marvel-repo "D:/ruta/marvelsdb-json-data" \
  --freakmod-repo "D:/ruta/freakmod"
```

## Archivos que se sincronizan por defecto

- `bot/data/marvelcdb_spanish.json`
- `bot/data/factions.json`
- `bot/data/sets.json`
- `bot/data/types.json`
- `bot/data/packs.json`
- `bot/data/IC_file_id_cache.json`
- `bot/data/failed_cards.json`

Puedes añadir más archivos con `--sync-file`.
