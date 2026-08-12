import json
import requests
from cachetools import TTLCache
from io import BytesIO
from PIL import Image
import os
import sys
import time
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Configuración de caché
cache_cartas = TTLCache(maxsize=100, ttl=1800)
cache_imagenes = {}
marca_agua_path = "marca_agua.png"
REQUEST_TIMEOUT = (10, 60)
PROGRESS_EVERY = 25
FAILED_CARDS_FILE = "failed_cards.json"

# Cargar identificadores ya presentes para evitar descargas duplicadas
def cargar_ids_existentes(path="IC_file_id_cache.json"):
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return set(json.load(f).keys())
    return set()

# Cargar y guardar tarjetas con errores
def cargar_tarjetas_fallidas(path=FAILED_CARDS_FILE):
    """Carga el registro de tarjetas que han fallado."""
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return {}
    return {}

def guardar_tarjetas_fallidas(failed_dict, path=FAILED_CARDS_FILE):
    """Guarda el registro de tarjetas que han fallado."""
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(failed_dict, f, ensure_ascii=False, indent=2)
    except IOError as e:
        print(f"⚠️ Error al guardar tarjetas fallidas: {e}", flush=True)

def registrar_fallo_tarjeta(codigo, error_msg, failed_dict):
    """Registra una tarjeta fallida con timestamp y mensaje de error."""
    failed_dict[codigo] = {
        "timestamp": datetime.now().isoformat(),
        "error": str(error_msg)
    }
    guardar_tarjetas_fallidas(failed_dict)

IDS_DESCARGADOS = cargar_ids_existentes()
TARJETAS_FALLIDAS = cargar_tarjetas_fallidas()

# Cargar cartas y traducciones desde archivos JSON
def cargar_datos():
    archivos = ["marvelcdb_spanish.json", "types.json", "factions.json", "sets.json", "packs.json"]
    datos = {}
    for archivo in archivos:
        with open(archivo, "r", encoding="utf-8") as f:
            datos[archivo] = json.load(f)
    return datos

datos = cargar_datos()
CARTAS = datos["marvelcdb_spanish.json"]["cartas"]

# Índice de cartas por código
cartas_por_codigo = {carta['code']: carta for carta in CARTAS if 'code' in carta}

def obtener_info_carta(codigo):
    return cartas_por_codigo.get(codigo, None)

def obtener_posicion_optima(imagen, marca_agua, tipo_carta):
    ancho_imagen, alto_imagen = imagen.width, imagen.height
    ancho_marca, alto_marca = marca_agua.width, marca_agua.height

    if alto_imagen > ancho_imagen:  # Imagen vertical
        x = (ancho_imagen // 2) - (ancho_marca // 2)
        if tipo_carta == "Mejora":
            y = alto_imagen * 3 // 10 - (alto_marca // 2)
        elif tipo_carta in ["Aliado", "Esbirro"]:
            y = alto_imagen * 7 // 20 - (alto_marca // 2)
        elif tipo_carta == "Accesorio":
            y = alto_imagen * 5 // 20 - (alto_marca // 2)
            x = (ancho_imagen * 9 // 16) - (ancho_marca // 2)
        elif tipo_carta in ["Héroe", "Alter ego", None]:
            y = alto_imagen * 7 // 20 - (alto_marca // 2)
        else:
            y = alto_imagen * 7 // 20 - (alto_marca // 2)
    else:  # Imagen horizontal
        y = (alto_imagen // 2) - (alto_marca // 2)
        if tipo_carta == "Plan principal":
            x = (ancho_imagen * 3 // 4) - (ancho_marca // 2)
        else:
            x = (ancho_imagen * 1 // 4) - (ancho_marca // 2)
    return (x, y)

def agregar_marca_agua(image_data, marca_agua_path, tipo_carta):
    try:
        imagen = Image.open(BytesIO(image_data)).convert("RGBA")
        marca_agua = Image.open(marca_agua_path).convert("RGBA")
        alpha = 125
        r, g, b, a = marca_agua.split()
        a = a.point(lambda i: i * (alpha / 255))
        marca_agua = Image.merge("RGBA", (r, g, b, a))

        if imagen.height > imagen.width:
            factor = 1 / 2
        else:
            factor = 2 / 3

        max_width = int(imagen.width * factor)
        max_height = int(imagen.height * factor)
        marca_agua.thumbnail((max_width, max_height))

        posicion = obtener_posicion_optima(imagen, marca_agua, tipo_carta)
        imagen.paste(marca_agua, posicion, marca_agua)

        imagen_io = BytesIO()
        imagen.save(imagen_io, format="PNG")
        imagen_io.seek(0)
        return imagen_io.getvalue()
    except Exception as e:
        print(f"Error al agregar marca de agua: {e}")
        return image_data

def obtener_imagen(codigo_carta, tipo):
    if codigo_carta in cache_imagenes:
        return cache_imagenes[codigo_carta]

    url = f"https://cdn.jsdelivr.net/gh/alaintxu/mc-ocr@main/images/accepted/{codigo_carta}.webp"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 200:
            imagen_data = response.content
            imagen_data = agregar_marca_agua(imagen_data, marca_agua_path, tipo)
            cache_imagenes[codigo_carta] = imagen_data
            return imagen_data
        else:
            error_msg = f"HTTP {response.status_code}"
            registrar_fallo_tarjeta(codigo_carta, error_msg, TARJETAS_FALLIDAS)
            raise Exception(f"Error al obtener imagen: {response.status_code}")
    except requests.RequestException as e:
        error_msg = f"Red: {str(e)}"
        registrar_fallo_tarjeta(codigo_carta, error_msg, TARJETAS_FALLIDAS)
        print(f"Error de red al obtener imagen de {codigo_carta}: {e}", flush=True)
        return None
    except Exception as e:
        error_msg = f"Procesamiento: {str(e)}"
        registrar_fallo_tarjeta(codigo_carta, error_msg, TARJETAS_FALLIDAS)
        print(f"Error al obtener imagen de {codigo_carta}: {e}", flush=True)
        return None

def procesar_carta(codigo, tipo):
    # Saltar si la tarjeta ya ha fallado antes
    if codigo in TARJETAS_FALLIDAS:
        print(f"⏭️  Saltando {codigo} (falló previamente: {TARJETAS_FALLIDAS[codigo].get('error', 'desconocido')})", flush=True)
        return

    info = obtener_info_carta(codigo)
    if not info:
        error_msg = "Carta no encontrada en base de datos"
        registrar_fallo_tarjeta(codigo, error_msg, TARJETAS_FALLIDAS)
        print(f"No se encontró información para la carta {codigo}", flush=True)
        return

    inicio = time.perf_counter()
    print(f"Procesando {codigo}...", flush=True)
    imagen_con_marca = obtener_imagen(codigo, tipo)
    if imagen_con_marca:
        try:
            os.makedirs("imagenes_con_marca", exist_ok=True)
            with open(f"imagenes_con_marca/{codigo}.png", "wb") as f:
                f.write(imagen_con_marca)
            elapsed = time.perf_counter() - inicio
            print(f"Imagen guardada: {codigo}.png en {elapsed:.1f}s", flush=True)
        except Exception as e:
            error_msg = f"Guardar: {str(e)}"
            registrar_fallo_tarjeta(codigo, error_msg, TARJETAS_FALLIDAS)
            print(f"Error al guardar la imagen de {codigo}: {e}", flush=True)
    else:
        elapsed = time.perf_counter() - inicio
        print(f"No se pudo obtener la imagen para {codigo} tras {elapsed:.1f}s", flush=True)

def descargar_cartas_con_marca():
    total = len(cartas_por_codigo)
    pendientes = [codigo for codigo in sorted(cartas_por_codigo.keys()) if codigo not in IDS_DESCARGADOS]
    saltadas_por_fallo = [c for c in pendientes if c in TARJETAS_FALLIDAS]
    pendientes_procesables = [c for c in pendientes if c not in TARJETAS_FALLIDAS]

    print(f"Iniciando generacion de imagenes: {total} cartas totales", flush=True)
    print(f"Cartas ya cacheadas: {len(IDS_DESCARGADOS)}", flush=True)
    print(f"Cartas pendientes de procesar: {len(pendientes)}", flush=True)
    if saltadas_por_fallo:
        print(f"⏭️  Cartas saltadas por fallos previos: {len(saltadas_por_fallo)}", flush=True)
    print(f"Cartas a intentar procesar: {len(pendientes_procesables)}", flush=True)

    if not pendientes_procesables:
        print("No hay cartas nuevas para procesar.", flush=True)
        if saltadas_por_fallo:
            print(f"(Hay {len(saltadas_por_fallo)} cartas que fallan; consulta failed_cards.json)", flush=True)
        return

    started = time.perf_counter()
    processed = 0

    for index, codigo in enumerate(pendientes_procesables, start=1):
        carta = cartas_por_codigo[codigo]
        tipo = carta.get("type_code")
        procesar_carta(codigo, tipo)
        processed += 1

        if index == 1 or index % PROGRESS_EVERY == 0:
            elapsed = time.perf_counter() - started
            print(f"Progreso: {index}/{len(pendientes_procesables)} cartas pendientes, {processed} procesadas, {elapsed:.1f}s", flush=True)

    elapsed = time.perf_counter() - started
    print(f"Finalizado: {processed} procesadas, {elapsed:.1f}s en total", flush=True)
    if TARJETAS_FALLIDAS:
        print(f"⚠️  Hay {len(TARJETAS_FALLIDAS)} tarjetas en failed_cards.json que necesitan revisión", flush=True)

if __name__ == "__main__":
    descargar_cartas_con_marca()
