#!/usr/bin/env python3
import os
import sys
import json
import time
import asyncio
from datetime import datetime
from telegram import Bot
from telegram.error import RetryAfter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def log(msg: str) -> None:
    """Print con timestamp y flush inmediato, para poder distinguir en el
    log si el proceso avanza despacio o esta realmente atascado."""
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)

# —————————————————————————————————————————————
# Configuración
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_UPLOAD_CHAT_ID")
if not TOKEN or not CHAT_ID:
    raise RuntimeError("TELEGRAM_BOT_TOKEN y TELEGRAM_UPLOAD_CHAT_ID deben estar en el entorno.")
CHAT_ID = int(CHAT_ID)
IMAGES_DIR  = Path("./imagenes_con_marca")
CACHE_FILE  = Path("IC_file_id_cache.json")

# Cada cuántas imágenes guardamos el JSON
BATCH_SAVE = 10

# Tiempo máximo (segundos) para subir o borrar una única imagen antes de darla por atascada
PER_IMAGE_TIMEOUT = 30

# Cada cuántas imágenes nuevas se imprime un resumen de progreso (tiempo transcurrido, ETA)
PROGRESS_EVERY = 10
# Si no hay ninguna línea de log en este tiempo, se imprime un aviso de "sigo vivo"
HEARTBEAT_SECONDS = 20

# —————————————————————————————————————————————
async def main():
    bot = Bot(TOKEN)

    # 1) Carga el cache existente (si no existe, dict vacío)
    if CACHE_FILE.exists():
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            cache = json.load(f)
    else:
        cache = {}

    count = 0  # contador de nuevas imágenes procesadas
    fallidas = []  # imágenes que se saltaron por atasco/error
    MAX_RETRY_AFTER_ATTEMPTS = 5

    async def esperar_con_heartbeat(segundos: float, motivo: str) -> None:
        """Espera 'segundos' imprimiendo un aviso cada HEARTBEAT_SECONDS, para que
        el log no se quede en silencio mientras se respeta un flood control largo."""
        restante = segundos
        while restante > 0:
            paso = min(HEARTBEAT_SECONDS, restante)
            await asyncio.sleep(paso)
            restante -= paso
            if restante > 0:
                log(f"  ...sigo esperando ({motivo}), quedan {restante:.0f}s")

    async def subir_una(img_path, code):
        """Sube una imagen con reintentos por flood control. Puede lanzar excepción."""
        msg = None
        for attempt in range(1, MAX_RETRY_AFTER_ATTEMPTS + 1):
            try:
                with open(img_path, "rb") as img_file:
                    msg = await asyncio.wait_for(
                        bot.send_photo(
                            chat_id=CHAT_ID,
                            photo=img_file,
                            disable_notification=True
                        ),
                        timeout=PER_IMAGE_TIMEOUT,
                    )
                break
            except RetryAfter as e:
                wait = e.retry_after + 1
                log(f"Flood control ({attempt}/{MAX_RETRY_AFTER_ATTEMPTS}): esperando {wait}s...")
                await esperar_con_heartbeat(wait, f"flood control de {code}")
        if msg is None:
            raise RuntimeError(f"No se pudo subir {code} tras {MAX_RETRY_AFTER_ATTEMPTS} intentos por flood control")

        file_id = msg.photo[-1].file_id
        cache[code] = file_id

        # Borra el mensaje para no ensuciar el chat (si esto se atasca, no perdemos el file_id)
        try:
            await asyncio.wait_for(
                bot.delete_message(chat_id=CHAT_ID, message_id=msg.message_id),
                timeout=PER_IMAGE_TIMEOUT,
            )
        except Exception as e:
            log(f"Aviso: no se pudo borrar el mensaje de {code}: {e}")

    if not IMAGES_DIR.exists():
        log(f"{IMAGES_DIR} no existe todavia (no hubo imagenes nuevas que generar). Nada que subir.")
        todas = []
    else:
        todas = sorted(p for p in IMAGES_DIR.iterdir() if p.suffix.lower() == ".png")
    pendientes = [p for p in todas if p.stem not in cache]
    log(
        f"Total imagenes en carpeta: {len(todas)} | ya cacheadas: {len(todas) - len(pendientes)} | "
        f"pendientes de subir: {len(pendientes)}"
    )
    if not pendientes:
        log("No hay imagenes nuevas que subir.")

    inicio = time.monotonic()
    subidas_ok = 0

    try:
        # 2) Recorre la carpeta y sube sólo las nuevas
        for img_path in todas:
            code = img_path.stem
            if code in cache:
                count += 1
                continue                # ya cacheado

            log(f"Subiendo {img_path.name}... ({count + 1}/{len(todas)})")
            try:
                await subir_una(img_path, code)
            except asyncio.TimeoutError:
                log(f"Atascada: {code} no respondió en {PER_IMAGE_TIMEOUT}s, se salta.")
                fallidas.append(code)
                continue
            except Exception as e:
                log(f"Error subiendo {code}: {e}, se salta.")
                fallidas.append(code)
                continue

            # Pausa mínima para no chocar con rate limits
            await asyncio.sleep(1)

            count += 1
            subidas_ok += 1

            if subidas_ok % PROGRESS_EVERY == 0:
                transcurrido = time.monotonic() - inicio
                promedio = transcurrido / subidas_ok
                restantes = len(pendientes) - subidas_ok
                eta = promedio * restantes
                log(
                    f"Progreso: {subidas_ok}/{len(pendientes)} nuevas subidas "
                    f"({transcurrido:.0f}s transcurridos, ~{promedio:.1f}s/imagen, ETA ~{eta:.0f}s)"
                )

            # Guardado intermedio cada BATCH_SAVE
            if count % BATCH_SAVE == 0:
                log(f"Guardando cache parcial tras {count} imagenes...")
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(cache, f, indent=2, ensure_ascii=False)

    except Exception as e:
        # Capturamos cualquier error inesperado fuera del bucle y volcamos lo subido hasta ahora
        log(f"\nError inesperado: {e}")
        log("Guardando cache parcial antes de salir...")
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        sys.exit(1)

    # Guardado final
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

    total_transcurrido = time.monotonic() - inicio
    log(f"Hecho en {total_transcurrido:.0f}s. Ahora hay {len(cache)} imagenes cacheadas.")
    if fallidas:
        log(f"Se saltaron {len(fallidas)} imagenes por atasco/error: {', '.join(fallidas)}")

if __name__ == "__main__":
    asyncio.run(main())
