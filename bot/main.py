# =========================================
# BLOQUE 1: CARGA DE DATOS Y DEFINICIONES
# =========================================
import json
import logging
import asyncio
import re
import unicodedata
import os
from pathlib import Path
from threading import Lock
from dotenv import load_dotenv
from rapidfuzz import process, fuzz
from telegram import Update, Message, InlineQueryResultPhoto, InputTextMessageContent, InlineQueryResultCachedPhoto, InlineQueryResultArticle
from telegram.ext import Application, CommandHandler, CallbackContext, InlineQueryHandler
from cachetools import TTLCache
from telegram.constants import ParseMode
from live_update.soft_updater import run_soft_update, check_soft_update

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR.parent / ".env")
ADMIN_IDS_FILE = BASE_DIR / "admin_ids.txt"


def cargar_admin_ids() -> set[int]:
    """Carga IDs admin desde BOT_ADMIN_IDS y/o desde current/admin_ids.txt."""
    raw_values = []

    env_value = os.getenv("BOT_ADMIN_IDS", "")
    if env_value:
        raw_values.extend(env_value.replace("\n", ",").split(","))

    if ADMIN_IDS_FILE.exists():
        with open(ADMIN_IDS_FILE, "r", encoding="utf-8") as f:
            for line in f:
                clean = line.strip()
                if not clean or clean.startswith("#"):
                    continue
                raw_values.extend(clean.replace(" ", ",").split(","))

    return {int(value.strip()) for value in raw_values if value.strip().isdigit()}


BOT_ADMIN_IDS = cargar_admin_ids()
UPDATE_LOCK = Lock()

# Configuración de logging
logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

# Configuración de caché para cartas e imágenes
cache_cartas = TTLCache(maxsize=100, ttl=1800)
cache_imagenes = TTLCache(maxsize=100, ttl=600)

# Carga los archivos JSON necesarios con datos del juego
def cargar_datos():
    archivos = ["marvelcdb_spanish.json", "types.json", "factions.json", "sets.json", "packs.json"]
    datos = {}
    for archivo in archivos:
        with open(BASE_DIR / "data" / archivo, "r", encoding="utf-8") as f:
            datos[archivo] = json.load(f)
    return datos

# Cache de IDs de archivo previamente enviados en Telegram
with open(BASE_DIR / "data" / "IC_file_id_cache.json", encoding="utf-8") as f:
    cache_file_ids = json.load(f)

# Diccionarios y estructuras de datos

datos = cargar_datos()
CARTAS = datos["marvelcdb_spanish.json"]["cartas"]

trad_tipos = {t['code']: t['name'] for t in datos["types.json"]}; trad_tipos[''] = ''
trad_aspecto = {a['code']: a['name'] for a in datos["factions.json"]}; trad_aspecto[''] = ''
trad_sets = {s['code']: s['name'] for s in datos["sets.json"]}; trad_sets[''] = ''
trad_packs = {p['code']: p['name'] for p in datos["packs.json"]}; trad_packs[''] = ''

cartas_por_nombre = {}
cartas_por_codigo = {carta['code']: carta for carta in CARTAS if 'code' in carta}

# Indexa las cartas por nombre, incluyendo nombres secundarios (subname)
for carta in CARTAS:
    nombre = carta.get("name", "").lower()
    if len(nombre) > 2:
        cartas_por_nombre.setdefault(nombre, []).append(carta)
    if "subname" in carta:
        nombre_sub = f"{nombre} {carta['subname'].lower()}"
        cartas_por_nombre.setdefault(nombre_sub, []).append(carta)

# Diccionario para representar las etapas del juego
etapas = {'I': 1, 'II': 2, 'III': 3, 'IV': 4, 'A': 'A', 'B': 'B'}

# Preprocesamiento: crea una versión normalizada de nombres de cartas
cartas_por_nombre_norm = {}
def normalize(text: str) -> str:
    text = text.lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9\s]", "", text).strip()

for nombre, cartas in cartas_por_nombre.items():
    clave = normalize(nombre)
    cartas_por_nombre_norm.setdefault(clave, []).extend(cartas)


def recargar_datos_en_memoria() -> None:
    """Recarga datos y cache en memoria sin reiniciar el proceso del bot."""
    global datos
    global CARTAS
    global trad_tipos
    global trad_aspecto
    global trad_sets
    global trad_packs
    global cartas_por_nombre
    global cartas_por_codigo
    global cartas_por_nombre_norm
    global cache_file_ids

    with open(BASE_DIR / "data" / "IC_file_id_cache.json", encoding="utf-8") as f:
        cache_file_ids = json.load(f)

    nuevos_datos = cargar_datos()
    nuevas_cartas = nuevos_datos["marvelcdb_spanish.json"]["cartas"]
    nuevos_tipos = {t["code"]: t["name"] for t in nuevos_datos["types.json"]}
    nuevos_tipos[""] = ""
    nuevos_aspectos = {a["code"]: a["name"] for a in nuevos_datos["factions.json"]}
    nuevos_aspectos[""] = ""
    nuevos_sets = {s["code"]: s["name"] for s in nuevos_datos["sets.json"]}
    nuevos_sets[""] = ""
    nuevos_packs = {p["code"]: p["name"] for p in nuevos_datos["packs.json"]}
    nuevos_packs[""] = ""

    nuevas_cartas_por_nombre = {}
    nuevas_cartas_por_codigo = {carta["code"]: carta for carta in nuevas_cartas if "code" in carta}
    for carta in nuevas_cartas:
        nombre = carta.get("name", "").lower()
        if len(nombre) > 2:
            nuevas_cartas_por_nombre.setdefault(nombre, []).append(carta)
        if "subname" in carta:
            nombre_sub = f"{nombre} {carta['subname'].lower()}"
            nuevas_cartas_por_nombre.setdefault(nombre_sub, []).append(carta)

    nuevas_norm = {}
    for nombre, cartas in nuevas_cartas_por_nombre.items():
        clave = normalize(nombre)
        nuevas_norm.setdefault(clave, []).extend(cartas)

    datos = nuevos_datos
    CARTAS = nuevas_cartas
    trad_tipos = nuevos_tipos
    trad_aspecto = nuevos_aspectos
    trad_sets = nuevos_sets
    trad_packs = nuevos_packs
    cartas_por_nombre = nuevas_cartas_por_nombre
    cartas_por_codigo = nuevas_cartas_por_codigo
    cartas_por_nombre_norm = nuevas_norm

# ====================================================
# BLOQUE 2: FUNCIONES AUXILIARES (AYUDA, MENSAJES)
# ====================================================

async def auto_delete_message(message: Message, time: int):
    """Elimina un mensaje tras un retardo."""
    await asyncio.sleep(time)
    try:
        await message.delete()
    except Exception as e:
        logging.warning(f"No se pudo eliminar el mensaje: {e}")

async def ayuda(update: Update, context: CallbackContext):
    """Envía un mensaje de ayuda con todos los comandos disponibles."""
    mensaje = '''*Lista de comandos disponibles:*
- * @InfoCartas_bot* nombre/código 
    Este irá actualizandose mientras escribes y te saldrá un desplegable con las opciones y pinchas sobre la deseada.
    
    Si estas en móvil, te saldrá a pantalla completa y tendras que confirmar el envío, si estas en PC puede dejar pulsado sobre la imagen para verla en grande.
    
    Ejemplo: *@InfoCartas_bot* Vengadores
    
- */buscar* filtro=valor - Filtros por tipo, aspecto, rasgo, pack, set, coste, INT, ATQ, DEF y/o Vida, separado por comas.

    Ejemplo: /buscar set=SP//dr, tipo=evento, coste=2

- */ayuda* - Muestra esta ayuda.
- */mi_id* - Muestra tu ID de Telegram.
- */actualizar* - (Admin) Actualiza datos desde GitHub y recarga en memoria sin reiniciar.
- */check_update* - (Admin) Comprueba si hay cambios remotos sin aplicar actualización.
- */estado_update* - (Admin) Estado de la actualización en caliente.'''
    msg = await update.message.reply_text(mensaje, parse_mode="Markdown", )
    asyncio.create_task(auto_delete_message(msg, 60))
    await update.message.delete()


def usuario_autorizado(update: Update) -> bool:
    global BOT_ADMIN_IDS
    user = update.effective_user
    if not user:
        return False
    BOT_ADMIN_IDS = cargar_admin_ids()
    return user.id in BOT_ADMIN_IDS


async def actualizar_servidor(update: Update, context: CallbackContext) -> None:
    """Ejecuta actualización en caliente y recarga datos en memoria si hubo cambios."""
    if not usuario_autorizado(update):
        msg = await update.message.reply_text("No tienes permisos para este comando.", )
        asyncio.create_task(auto_delete_message(msg, 10))
        await update.message.delete()
        return

    acquired = UPDATE_LOCK.acquire(blocking=False)
    if not acquired:
        msg = await update.message.reply_text("Ya hay una actualización en curso. Espera a que termine.", )
        asyncio.create_task(auto_delete_message(msg, 10))
        await update.message.delete()
        return

    status_msg = await update.message.reply_text("Iniciando actualización en caliente...", )
    try:
        result = await asyncio.to_thread(run_soft_update, BASE_DIR)
        if result["status"] == "error":
            texto = "Error durante la actualización:\n" + "\n".join(result["messages"][-5:])
            await status_msg.edit_text(texto)
            return

        if result["changes_applied"]:
            recargar_datos_en_memoria()
            resumen = [
                "Actualización completada y datos recargados en memoria.",
                f"Repositorios actualizados: {', '.join(result['updated_repos'])}",
            ]
            if result["messages"]:
                resumen.append("Ultimo detalle: " + result["messages"][-1])
            await status_msg.edit_text("\n".join(resumen))
        else:
            await status_msg.edit_text("No se detectaron cambios en los repositorios. No se recargaron datos.")
    except Exception as e:
        logging.exception("Fallo en actualización en caliente")
        await status_msg.edit_text(f"Error inesperado al actualizar: {e}")
    finally:
        UPDATE_LOCK.release()
        await update.message.delete()


async def estado_actualizacion(update: Update, context: CallbackContext) -> None:
    """Muestra estado rápido de configuración de actualización en caliente."""
    if not usuario_autorizado(update):
        msg = await update.message.reply_text("No tienes permisos para este comando.", )
        asyncio.create_task(auto_delete_message(msg, 10))
        await update.message.delete()
        return

    estado_lock = "en curso" if UPDATE_LOCK.locked() else "inactiva"
    mensaje = (
        "Estado de actualización en caliente:\n"
        f"- Proceso: {estado_lock}\n"
        f"- Admin IDs cargados: {len(BOT_ADMIN_IDS)}\n"
        f"- Archivo admins: {ADMIN_IDS_FILE}\n"
        f"- Base dir: {BASE_DIR}"
    )
    msg = await update.message.reply_text(mensaje, )
    asyncio.create_task(auto_delete_message(msg, 20))
    await update.message.delete()


async def check_actualizacion(update: Update, context: CallbackContext) -> None:
    """Comprueba si hay cambios remotos sin ejecutar la actualización completa."""
    if not usuario_autorizado(update):
        msg = await update.message.reply_text("No tienes permisos para este comando.", )
        asyncio.create_task(auto_delete_message(msg, 10))
        await update.message.delete()
        return

    status_msg = await update.message.reply_text("Comprobando cambios remotos...", )
    try:
        result = await asyncio.to_thread(check_soft_update, BASE_DIR)
        if result["status"] == "error":
            texto = "Error al comprobar cambios:\n" + "\n".join(result["messages"][-5:])
            await status_msg.edit_text(texto)
            return

        if result["changes_detected"]:
            texto = "Hay cambios pendientes en:\n"
            texto += "\n".join(f"- {repo}" for repo in result["repos_with_changes"])
            texto += "\n\nUsa /actualizar para aplicarlos y recargar en memoria."
            await status_msg.edit_text(texto)
        else:
            await status_msg.edit_text("No hay cambios remotos en los repositorios configurados.")
    except Exception as e:
        logging.exception("Fallo en check de actualización")
        await status_msg.edit_text(f"Error inesperado al comprobar cambios: {e}")
    finally:
        await update.message.delete()


async def mi_id(update: Update, context: CallbackContext) -> None:
    """Devuelve el ID de Telegram del usuario que ejecuta el comando."""
    user = update.effective_user
    if not user:
        msg = await update.message.reply_text("No pude obtener tu ID.")
        asyncio.create_task(auto_delete_message(msg, 10))
        await update.message.delete()
        return

    msg = await update.message.reply_text(f"Tu Telegram ID es: {user.id}")
    asyncio.create_task(auto_delete_message(msg, 30))
    await update.message.delete()

async def generar_mensaje_carta(carta):
    """Genera un mensaje con información formateada de una carta."""
    mensaje = f"*{carta['name']}* ({carta['code']})\n"
    mensaje += f"*Rasgos:* {carta.get('traits', '')}\n"
    mensaje += f"*Texto:*\n{carta.get('text', 'Sin descripción.')}\n"
    if "errata" in carta:
        mensaje += f"\n*ERRATA:*\n{carta.get('errata')}\n"
    return mensaje


# ==============================================================
# BLOQUE 3: FUNCIONES DE COINCIDENCIAS Y OBTENCIÓN DE RESULTADOS
# ==============================================================

def extraer_nombre_etapa(nombre_carta, etapas):
    """Extrae nombre base y etapa si la carta la contiene (I, II, A, B...)."""
    regex_etapa = re.compile(r"( I| II| III| IV| A| B)$")
    match = regex_etapa.search(nombre_carta)
    if match:
        return nombre_carta[:match.start()].strip().lower(), etapas.get(match.group().strip())
    return nombre_carta.lower(), None

def buscar_coincidencias_exactas(nombre_base, cartas_por_nombre_norm, seen):
    """Primero busca substring directo, luego fuzzy con token_set_ratio."""
    nombre_base_norm = normalize(nombre_base)
    coincidencias = []
    for clave_norm, lista in cartas_por_nombre_norm.items():
        if nombre_base_norm in clave_norm:
            for carta in lista:
                key = json.dumps(carta, sort_keys=True)
                if key not in seen:
                    seen.add(key); coincidencias.append(carta)
    if not coincidencias:
        mejores = process.extract(nombre_base_norm, list(cartas_por_nombre_norm.keys()), scorer=fuzz.token_set_ratio, limit=10)
        for match, score, _ in mejores:
            if score >= 90:
                for carta in cartas_por_nombre_norm[match]:
                    key = json.dumps(carta, sort_keys=True)
                    if key not in seen:
                        seen.add(key); coincidencias.append(carta)
    return coincidencias

def buscar_coincidencias_relajadas(nombre_base, cartas_por_nombre_norm, seen):
    """Similar a la anterior pero usando fuzzy parcial (partial_ratio)."""
    nombre_base_norm = normalize(nombre_base)
    coincidencias = []
    for clave_norm, lista in cartas_por_nombre_norm.items():
        if nombre_base_norm in clave_norm:
            for carta in lista:
                key = json.dumps(carta, sort_keys=True)
                if key not in seen:
                    seen.add(key); coincidencias.append(carta)
    mejores = process.extract(nombre_base_norm, list(cartas_por_nombre_norm.keys()), scorer=fuzz.partial_ratio, limit=10)
    for match, score, _ in mejores:
        if score >= 80:
            for carta in cartas_por_nombre_norm[match]:
                key = json.dumps(carta, sort_keys=True)
                if key not in seen:
                    seen.add(key); coincidencias.append(carta)
    return coincidencias

def filtrar_por_etapa(coincidencias, etapa):
    return [c for c in coincidencias if c.get("stage") == etapa] if etapa else coincidencias

def cartas_multiples_buscar(codigo, lista):
    """Expande a cartas con varias caras a/b/c/d."""
    if codigo[-1] in ['a', 'b', 'c', 'd']:
        for letra in ['a', 'b', 'c', 'd']:
            doble = codigo[:-1] + letra
            if doble in cartas_por_codigo:
                lista.append(cartas_por_codigo[doble])
    return lista

def expandir_coincidencias(coincidencias):
    return cartas_multiples_buscar(coincidencias[0]['code'], coincidencias.copy()) if coincidencias else []

def obtener_resultado_final(coincidencias):
    """Elimina duplicados y ordena por código."""
    return sorted({c['code']: c for c in coincidencias}.values(), key=lambda x: x['code'])

def obtener_carta(nombre_carta, cartas_por_nombre, etapas):
    seen = set()
    nombre_base, etapa = extraer_nombre_etapa(nombre_carta, etapas)
    exactas = buscar_coincidencias_exactas(nombre_base, cartas_por_nombre_norm, seen)
    if exactas:
        exactas = expandir_coincidencias(filtrar_por_etapa(exactas, etapa))
        return obtener_resultado_final(exactas), True
    relajadas = buscar_coincidencias_relajadas(nombre_base, cartas_por_nombre_norm, seen)
    if relajadas:
        relajadas = expandir_coincidencias(filtrar_por_etapa(relajadas, etapa))
        return obtener_resultado_final(relajadas), False
    return None, False

def obtener_codigo_carta(carta):
    return carta['duplicate_of'] if 'duplicate_of' in carta else carta['code']

def codigo_carta_duplicados(codigo):
    return obtener_codigo_carta(cartas_por_codigo[codigo])

def comprobar_villano(carta):
    fases = {"1": "I", "2": "II", "3": "III", "4": "IV", "A": "A", "B": "B", "sin": ""}
    nombre = carta['name']
    stage = fases.get(str(carta.get('stage', 'sin')))
    if nombre == "Espiral":
        return f"{nombre} {stage} {carta['traits'].split('.')[0]}"
    elif nombre in ["El Coleccionista", "Barón Zemo"] and stage in ["A", "B"]:
        return f"{nombre} {stage} {carta['traits'].split('.')[1]}"
    return f"{nombre} {stage}" if stage else nombre

def obtener_info_carta(codigo):
    return cartas_por_codigo.get(codigo)



# ======================================
# BLOQUE 4: MANEJO DE CONSULTAS INLINE
# ======================================

CODE_RE = re.compile(r"^\d{5}[abcd]?$", re.IGNORECASE)  # Coincide con códigos como 12345 o 12345a

async def inline_query_handler(update: Update, context: CallbackContext) -> None:
    """Responde a una consulta inline del usuario con resultados de cartas."""
    q = update.inline_query.query.strip()
    if not q:
        return

    items = []
    # 1) Si es código exacto (por ejemplo: 01001a)
    if CODE_RE.match(q):
        codigo = codigo_carta_duplicados(q.lower())
        carta = cartas_por_codigo.get(codigo)
        if carta:
            caption = await generar_mensaje_carta(carta)
            file_id = cache_file_ids.get(codigo)
            title = f"{carta['name']} ({codigo})"
            if file_id:
                items.append(InlineQueryResultCachedPhoto(
                    id=f"code_{codigo}",
                    photo_file_id=file_id,
                    title=title,
                    caption=caption,
                    parse_mode="Markdown",
                ))
            else:
                items.append(InlineQueryResultArticle(
                    id=f"code_{codigo}",
                    title=title,
                    input_message_content=InputTextMessageContent(
                        message_text=caption,
                        parse_mode="Markdown",
                    ),
                ))
            await update.inline_query.answer(results=items, cache_time=0, is_personal=True)
            return

    # 2) Si no es código, buscar por nombre normalizado
    resultados, exacto = obtener_carta(q, cartas_por_nombre, etapas)
    if not resultados:
        return

    for idx, carta in enumerate(resultados[:20]):
        codigo = obtener_codigo_carta(carta)
        info = obtener_info_carta(codigo)
        title = f"{comprobar_villano(carta)} ({codigo})"
        caption = await generar_mensaje_carta(carta)

        if info.get('type_code', '') not in ['villain', 'treachery', 'side_scheme', 'obligation', 'minion', 'main_scheme', 'hero', 'environment', 'attachment', 'alter_ego']:
            title += f" - Aspecto: {trad_aspecto.get(info.get('faction_code', ''), '')}"
        else:
            title += f" - Set: {trad_sets.get(info.get('set_code', ''), '')}"
        if codigo in cache_file_ids:
            items.append(InlineQueryResultCachedPhoto(
                id=f"{codigo}_{idx}",
                photo_file_id=cache_file_ids[codigo],
                title=title,
                caption=caption,
                parse_mode="Markdown",
            ))
        else:
            items.append(InlineQueryResultArticle(
                id=f"{codigo}_{idx}",
                title=title,
                input_message_content=InputTextMessageContent(
                    message_text=caption,
                    parse_mode="Markdown",
                ),
            ))

    await update.inline_query.answer(results=items, cache_time=1, is_personal=True)


# ===================================================
# BLOQUE 5: BÚSQUEDA POR FILTROS Y CUMPLIMIENTO
# ===================================================

async def buscar_cartas_por_filtro(update: Update, context: CallbackContext) -> None:
    """Maneja el comando /buscar con filtros como tipo, aspecto, coste, etc."""
    parametros = update.message.text.strip().split(maxsplit=1)
    if len(parametros) < 2:
        msg = await update.message.reply_text("Debes proporcionar al menos un filtro de búsqueda.", parse_mode="Markdown", )
        asyncio.create_task(auto_delete_message(msg, 10))
        await update.message.delete()
        return

    filtros_texto = parametros[1]
    filtros = {}
    for parametro in filtros_texto.split(","):
        clave_valor = parametro.strip().split("=", 1)
        if len(clave_valor) == 2:
            filtros[clave_valor[0].strip().lower()] = clave_valor[1].strip().lower()

    if not filtros:
        msg = await update.message.reply_text("No se encontraron filtros válidos.", parse_mode="Markdown", )
        asyncio.create_task(auto_delete_message(msg, 10))
        await update.message.delete()
        return

    cartas_filtradas = [carta for carta in CARTAS if cumple_filtros(carta, filtros)]

    if not cartas_filtradas:
        msg = await update.message.reply_text("No se encontraron cartas con los filtros proporcionados.", parse_mode="Markdown", )
        asyncio.create_task(auto_delete_message(msg, 10))
        await update.message.delete()
        return

    mensaje = "Cartas encontradas por la búsqueda '"
    mensaje += ", ".join(f"{k}={v}" for k, v in filtros.items()) + "':\n"
    for carta in cartas_filtradas[:10]:
        mensaje += f"- *{carta['name']}* ({carta['code']})\n"
    if len(cartas_filtradas) > 10:
        mensaje += f"\nSe encontraron {len(cartas_filtradas)} cartas. Usa filtros más específicos."

    msg = await update.message.reply_text(mensaje, parse_mode="Markdown", )
    asyncio.create_task(auto_delete_message(msg, 40))
    await update.message.delete()


def cumple_filtros(carta, filtros):
    """Evalúa si una carta cumple con todos los filtros proporcionados."""
    for clave, valor in filtros.items():
        if clave == "tipo" and trad_tipos.get(carta.get("type_code", ''), '').lower() != valor:
            return False
        elif clave == "coste" and str(carta.get("cost", '')) != str(valor):
            return False
        elif clave == "atq" and str(carta.get("attack", '')) != str(valor):
            return False
        elif clave == "int" and str(carta.get("thwart", '')) != str(valor):
            return False
        elif clave == "def" and str(carta.get("defense", '')) != str(valor):
            return False
        elif clave == "vida" and str(carta.get("health", '')) != str(valor):
            return False
        elif clave == "aspecto" and trad_aspecto.get(carta.get("faction_code", ''), '').lower() != valor:
            return False
        elif clave == "set" and trad_sets.get(carta.get("set_code", ''), '').lower() != valor:
            return False
        elif clave == "pack" and trad_packs.get(carta.get("pack_code", ''), '').lower() != valor:
            return False
        elif clave == "rasgo":
            rasgos = [r.strip().lower().rstrip(".") for r in carta.get("traits", "").split(". ")]
            if valor not in rasgos:
                return False
    return True


# ===================================
# BLOQUE FINAL: PUNTO DE ENTRADA MAIN
# ===================================

def main():
    TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
    if not TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN no esta configurado (variable de entorno o .env).")
    application = Application.builder().token(TOKEN).build()
    application.add_handler(CommandHandler("ayuda", ayuda))
    application.add_handler(CommandHandler("mi_id", mi_id))
    application.add_handler(CommandHandler("buscar", buscar_cartas_por_filtro))
    application.add_handler(CommandHandler("actualizar", actualizar_servidor))
    application.add_handler(CommandHandler("check_update", check_actualizacion))
    application.add_handler(CommandHandler("estado_update", estado_actualizacion))
    application.add_handler(InlineQueryHandler(inline_query_handler))
    application.run_polling()


if __name__ == "__main__":
    main()
