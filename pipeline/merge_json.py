import os
import json
import requests

# Diccionario con las sustituciones
REPLACEMENTS = {"<b><i>": "_*", "</i></b>": "*_", "[[": "_", "]]": "_", 
    "<i>": "_", "</i>": "_","<em>":"_","</em>":"_",
    "[[[": "_", "]]]": "_", "<b>": "*", "</b>": "*",
    "<hr />": "------------------------------------------",
    "[physical]": "👊", "[energy]": "⚡",
    "[mental]": "🔬", "[wild]": "✳️",
    "[boost]": "🌶", "[star]": "★", "[per_hero]": "👤", "*_":"_", "_*":"_"
}

# Función para reemplazar texto según el diccionario

def replace_text(text):
    """Reemplaza caracteres según el diccionario REPLACEMENTS."""
    if isinstance(text, str):
        for old, new in REPLACEMENTS.items():
            text = text.replace(old, new)
    return text

def replace_text_in_json(data):
    """Recorre y modifica todos los valores de un JSON de manera recursiva."""
    if isinstance(data, dict):
        return {key: replace_text_in_json(value) for key, value in data.items()}
    elif isinstance(data, list):
        return [replace_text_in_json(item) for item in data]
    else:
        return replace_text(data)

def merge_json_files(input_folder, output_file):
    merged_data = {"cartas": []}
    
    if not os.path.exists(input_folder):
        print(f"Error: La carpeta '{input_folder}' no existe.")
        return

    json_files = [f for f in os.listdir(input_folder) if f.endswith(".json")]

    if not json_files:
        print("No se encontraron archivos JSON en la carpeta.")
        return

    for filename in json_files:
        file_path = os.path.join(input_folder, filename)

        try:
            with open(file_path, "r", encoding="utf-8") as file:
                data = json.load(file)

                if isinstance(data, list):
                    merged_data["cartas"].extend(data)
                elif isinstance(data, dict):
                    merged_data["cartas"].append(data)
                else:
                    print(f"Advertencia: El archivo {filename} no contiene una lista o un diccionario válido.")
        except json.JSONDecodeError:
            print(f"Error: El archivo {filename} contiene un JSON inválido y será omitido.")
        except Exception as e:
            print(f"Error al leer {filename}: {e}")

    # Aplicar reemplazos de texto
    merged_data = replace_text_in_json(merged_data)

    try:
        with open(output_file, "w", encoding="utf-8") as output:
            json.dump(merged_data, output, indent=4, ensure_ascii=False)
        print(f"Archivos JSON combinados, procesados y actualizados en '{output_file}'")
    except Exception as e:
        print(f"Error al escribir el archivo de salida: {e}")

# Uso del script
input_folder = "updated_json"  # Carpeta con los JSON
output_file = "marvelcdb_spanish.json"  # Archivo de salida

merge_json_files(input_folder, output_file)
