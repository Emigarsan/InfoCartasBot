# Sistema de Tracking de Tarjetas Fallidas

## Descripción
El archivo `bot/data/failed_cards.json` registra automáticamente las tarjetas que fallan durante el proceso de marca de agua. Esto evita que se reintente procesar tarjetas que siempre fallan (por ejemplo, imágenes corruptas, servidores caídos, etc).

## Funcionamiento

### Registro Automático
Cuando `marca_agua_v2.py` falla al procesar una tarjeta, guarda:
- **Código de tarjeta**: Identificador único (ej: `01001`, `03045`)
- **Timestamp**: Fecha y hora del error
- **Motivo del error**: Descripción del problema

### Ejemplo de contenido
```json
{
  "01234": {
    "timestamp": "2026-05-04T14:23:45.123456",
    "error": "HTTP 404"
  },
  "05678": {
    "timestamp": "2026-05-04T14:25:12.654321",
    "error": "Red: Read timeout"
  }
}
```

### Cómo funciona en la ejecución
1. **Carga inicial**: El script carga `failed_cards.json` al iniciar
2. **Filtrado**: Excluye tarjetas fallidas de la lista a procesar
3. **Salto silencioso**: Muestra un icono `⏭️` cuando salta una tarjeta conocida
4. **Registro de nuevos fallos**: Si una tarjeta falla nuevamente, se actualiza el timestamp

### Salida de consola
```
Cartas ya cacheadas: 2840
Cartas pendientes de procesar: 50
⏭️  Cartas saltadas por fallos previos: 12
Cartas a intentar procesar: 38
```

## Gestión Manual

### Para reintentar una tarjeta específica
1. Abre `bot/data/failed_cards.json`
2. Busca el código de tarjeta que deseas reintentar
3. Elimina esa línea (ej: `"01234": {...},`)
4. Guarda el archivo
5. Ejecuta `marca_agua_v2.py` nuevamente

### Para limpiar todos los fallos y reintentar
```bash
# Opción 1: Eliminar el archivo completamente
del bot\data\failed_cards.json

# Opción 2: O desde Python
import os
os.remove("bot/data/failed_cards.json")
```
Luego ejecuta `marca_agua_v2.py` y reintentará todas las tarjetas.

### Para revisar qué tarjetas están fallando
```bash
# Desde PowerShell
Get-Content bot/data/failed_cards.json | convertfrom-json | keys
```

## Errores Comunes

| Error | Causa | Solución |
|-------|-------|----------|
| `HTTP 404` | Imagen no existe en CDN | Probablemente es una tarjeta deprecated |
| `HTTP 429` | Rate limit (demasiadas solicitudes) | Espera y reintenta manualmente después |
| `Red: Read timeout` | Servidor lento o caído | Reintenta cuando el servidor esté disponible |
| `Guardar: [PermissionError]` | Carpeta sin permisos | Verifica permisos de `imagenes_con_marca/` |
| `Procesamiento: [error]` | Error en marca de agua (Pillow) | Podría ser imagen corrupta en CDN |

## Interacción con el Orquestador

El archivo `deploy/run_local_sftp_update.py` respeta `failed_cards.json`:
- No modifica el archivo de fallos
- Simplemente permite que `marca_agua_v2.py` lo gestione
- Si deseas limpiar fallos antes de una actualización completa, hazlo manualmente antes de ejecutar el orquestador

## Ejemplo Completo

```bash
# 1. Ejecutar actualización normal
python deploy/run_local_sftp_update.py
# Salida: "⏭️  Cartas saltadas por fallos previos: 8"

# 2. Revisar qué tarjetas fallaron
cat bot/data/failed_cards.json

# 3. Decidir si reintentar una específica
# Editar failed_cards.json y eliminar esa entrada

# 4. Ejecutar de nuevo
python deploy/run_local_sftp_update.py
```

