# InfoCartasBot

A Telegram inline bot that lets Marvel Champions LCG players search [MarvelCDB](https://marvelcdb.com/) cards, in Spanish, directly from any chat — no need to add the bot to a group, just type `@InfoCartas_bot <card name>` in any conversation.

🇪🇸 Versión en español: [README.es.md](README.es.md)

## What it does

- **Inline card search** with fuzzy matching (`rapidfuzz`) — typo-tolerant, matches by name or partial name.
- **Filtered search** via `/buscar` (e.g. `/buscar set=SP//dr, tipo=evento, coste=2`) across type, faction, trait, pack, set and stats.
- **Watermarked card images**, uploaded once to Telegram and cached by `file_id` so repeated lookups don't re-upload images.
- **Hot data updates**: an admin-only `/actualizar` command pulls fresh card/translation data from two external repositories and reloads it into the running process — no restart needed. See [ARCHITECTURE.md](ARCHITECTURE.md) for how that pipeline works and why the project depends on those external repos.

## Project layout

```
bot/        Live Telegram bot (entry point: bot/main.py) + runtime data
pipeline/   Offline data-prep pipeline that builds bot/data/marvelcdb_spanish.json
            from two external repos (see ARCHITECTURE.md)
deploy/     Optional local SFTP deploy orchestrator, alternative to the in-bot updater
docs/       Additional documentation
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env   # fill in TELEGRAM_BOT_TOKEN and BOT_ADMIN_IDS
```

`bot/data/` already ships with a built card dataset, so the bot runs standalone without needing the external repos — those are only required to *regenerate* the dataset (see [ARCHITECTURE.md](ARCHITECTURE.md)).

## Running

```bash
cd bot
python main.py
```

## Tech stack

Python, [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot), `rapidfuzz` (fuzzy search), `cachetools` (in-memory caching), `Pillow` (image watermarking), `python-dotenv`.

## License

MIT — see [LICENSE](LICENSE).
