#!/usr/bin/env python3
import os
import sys
import json
import asyncio
from telegram import Bot
from telegram.error import RetryAfter
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

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

    try:
        # 2) Recorre la carpeta y sube sólo las nuevas
        for img_path in sorted(IMAGES_DIR.iterdir()):
            if img_path.suffix.lower() != ".png":
                continue

            code = img_path.stem
            if code in cache:
                print(f"{code} ya cacheado")
                count += 1
                continue                # ya cacheado

            print(f"Subiendo {img_path.name}...")
            # 3) Intento normal, con manejo de flood control
            try:
                with open(img_path, "rb") as img_file:
                    msg = await bot.send_photo(
                        chat_id=CHAT_ID,
                        photo=img_file,
                        disable_notification=True
                    )
            except RetryAfter as e:
                wait = e.retry_after + 1
                print(f"Flood control: esperando {wait}s...")
                await asyncio.sleep(wait)
                with open(img_path, "rb") as img_file:
                    msg = await bot.send_photo(
                        chat_id=CHAT_ID,
                        photo=img_file,
                        disable_notification=True
                    )

            # 4) Extrae el file_id y cachea
            file_id = msg.photo[-1].file_id
            cache[code] = file_id

            # 5) Borra el mensaje para no ensuciar el chat
            await bot.delete_message(chat_id=CHAT_ID, message_id=msg.message_id)

            # 6) Pausa mínima para no chocar con rate limits
            await asyncio.sleep(1)

            count += 1
            # 7) Guardado intermedio cada BATCH_SAVE
            if count % BATCH_SAVE == 0:
                print(f"Guardando cache parcial tras {count} imagenes...")
                with open(CACHE_FILE, "w", encoding="utf-8") as f:
                    json.dump(cache, f, indent=2, ensure_ascii=False)

    except Exception as e:
        # Capturamos cualquier error (timeout u otro) y volcamos lo subido hasta ahora
        print(f"\nError inesperado: {e}")
        print("Guardando cache parcial antes de salir...")
        with open(CACHE_FILE, "w", encoding="utf-8") as f:
            json.dump(cache, f, indent=2, ensure_ascii=False)
        sys.exit(1)

    # 8) Guardado final
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, indent=2, ensure_ascii=False)

    print(f"\nHecho. Ahora hay {len(cache)} imagenes cacheadas.")

if __name__ == "__main__":
    asyncio.run(main())
