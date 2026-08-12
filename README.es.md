# InfoCartasBot

Bot de Telegram en modo inline que permite a jugadores de Marvel Champions LCG buscar cartas de [MarvelCDB](https://marvelcdb.com/) en español, directamente desde cualquier chat — sin añadir el bot a un grupo, basta con escribir `@InfoCartas_bot <nombre de carta>` en cualquier conversación.

🇬🇧 English version: [README.md](README.md)

## Qué hace

- **Búsqueda inline** con coincidencia difusa (`rapidfuzz`) — tolera errores tipográficos y coincide por nombre completo o parcial.
- **Búsqueda con filtros** vía `/buscar` (ej: `/buscar set=SP//dr, tipo=evento, coste=2`) por tipo, aspecto, rasgo, pack, set y estadísticas.
- **Imágenes de carta con marca de agua**, subidas una vez a Telegram y cacheadas por `file_id` para que las búsquedas repetidas no vuelvan a subir la imagen.
- **Actualización de datos en caliente**: el comando `/actualizar` (solo administradores) descarga datos frescos de cartas/traducciones desde dos repositorios externos y los recarga en el proceso en ejecución, sin reiniciar. Ver [ARCHITECTURE.md](ARCHITECTURE.md) (en inglés) para el detalle de ese pipeline y por qué el proyecto depende de esos repos externos.

## Estructura del proyecto

```
bot/        Bot de Telegram en ejecución (punto de entrada: bot/main.py) + datos en tiempo de ejecución
pipeline/   Pipeline offline de preparación de datos que genera bot/data/marvelcdb_spanish.json
            a partir de dos repos externos (ver ARCHITECTURE.md)
deploy/     Orquestador opcional de despliegue local por SFTP, alternativa al actualizador integrado
docs/       Documentación adicional
```

## Puesta en marcha

```bash
pip install -r requirements.txt
cp .env.example .env   # rellena TELEGRAM_BOT_TOKEN y BOT_ADMIN_IDS
```

`bot/data/` ya incluye un dataset de cartas generado, así que el bot funciona de forma independiente sin necesitar los repos externos — esos solo son necesarios para *regenerar* el dataset (ver [ARCHITECTURE.md](ARCHITECTURE.md)).

## Ejecución

```bash
cd bot
python main.py
```

## Stack técnico

Python, [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot), `rapidfuzz` (búsqueda difusa), `cachetools` (caché en memoria), `Pillow` (marca de agua en imágenes), `python-dotenv`.

## Licencia

MIT — ver [LICENSE](LICENSE).
