import os
import json
import sys
from pathlib import Path
from dotenv import load_dotenv

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

SCRIPT_DIR = Path(__file__).resolve().parent
load_dotenv(SCRIPT_DIR.parent / ".env")

# Configuración de las rutas. Por defecto asume que los dos repos externos
# estan clonados junto a este proyecto (ver ARCHITECTURE.md); se puede
# sobreescribir con MARVELCDB_REPO_DIR / TRANSLATION_REPO_DIR.
base_repo_dir = os.getenv("MARVELCDB_REPO_DIR", str(SCRIPT_DIR / "marvelsdb-json-data"))
translation_repo_dir = os.getenv("TRANSLATION_REPO_DIR", str(SCRIPT_DIR.parent / "freakmod"))
original_folder = base_repo_dir +  "/pack"
translations_folder = translation_repo_dir +  "/translations/es/pack"
updated_folder = "updated_json"

print(f"Usando original_folder={original_folder}")
print(f"Usando translations_folder={translations_folder}")

def cargar_json(ruta):
    """Carga un archivo JSON y maneja errores."""
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        print(f"Error al leer {ruta}: {e}")
        return None

def actualizar_json(original_file, translation_file, output_file):
    """Actualiza el JSON original con las traducciones basadas en 'code' y guarda el resultado en otra carpeta."""
    
    original_data = cargar_json(original_file)
    translation_data = cargar_json(translation_file)

    if original_data is None or translation_data is None:
        return

    if not isinstance(original_data, list) or not isinstance(translation_data, list):
        print(f"⚠️ Los archivos {original_file} y {translation_file} deben contener listas JSON.")
        return
    
    # Crear un diccionario con las traducciones basadas en 'code'
    translation_dict = {item["code"]: item for item in translation_data if "code" in item}

    # Actualizar los valores del JSON original basándose en 'code'
    for item in original_data:
        if "code" in item and item["code"] in translation_dict:
            item.update(translation_dict[item["code"]])  # Fusionar los datos manteniendo la estructura original

    # Guardar el archivo actualizado en la carpeta de salida
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(original_data, f, ensure_ascii=False, indent=4)
    
    print(f"Archivo actualizado guardado en: {output_file}")

def procesar_archivos():
    """Recorre los archivos en la carpeta de traducciones y actualiza los JSON originales."""
    if not os.path.exists(original_folder) or not os.path.exists(translations_folder):
        print("Asegurate de que ambas carpetas existen.")
        return

    # Crear la carpeta de salida si no existe
    os.makedirs(updated_folder, exist_ok=True)

    archivos_traduccion = os.listdir(translations_folder)

    for archivo in archivos_traduccion:
        if archivo.endswith(".json"):
            original_path = os.path.join(original_folder, archivo)
            translation_path = os.path.join(translations_folder, archivo)
            updated_path = os.path.join(updated_folder, archivo)

            if os.path.exists(original_path):
                print(f"Procesando {archivo}...")
                actualizar_json(original_path, translation_path, updated_path)
            else:
                print(f"No se encontro el archivo original para {archivo}. Se omite.")

if __name__ == "__main__":
    procesar_archivos()
