# InfoCartasBot

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](https://www.python.org/)
[![python-telegram-bot](https://img.shields.io/badge/python--telegram--bot-20%2B-2CA5E0?logo=telegram&logoColor=white)](https://github.com/python-telegram-bot/python-telegram-bot)

A Telegram **inline bot** that lets Marvel Champions LCG players search [MarvelCDB](https://marvelcdb.com/) cards, in Spanish, directly from any chat. No need to add a bot to a group — just type `@InfoCartas_bot <card name>` inside any conversation, group or DM, and pick a card from the dropdown.

🇪🇸 Versión en español: [README.es.md](README.es.md)

## Demo

<p align="center">
  <img src="docs/demo-search.png" alt="Inline search dropdown for @InfoCartas_bot" width="45%">
  &nbsp;&nbsp;
  <img src="docs/demo-result.png" alt="Card sent by InfoCartasBot in a chat" width="45%">
</p>
<p align="center"><em>Inline search → card result, watermarked and sent straight into the chat.</em></p>

## Table of contents

- [What it does](#what-it-does)
- [Commands](#commands)
- [Architecture](#architecture)
- [Project layout](#project-layout)
- [Setup](#setup)
- [Running](#running)
- [Tech stack](#tech-stack)
- [Highlights](#highlights)
- [License](#license)

## What it does

- **Inline card search** with fuzzy, accent/case-insensitive matching (`rapidfuzz`) — typo-tolerant, matches by full or partial name, including secondary card names.
- **Filtered search** via `/buscar` across type, faction, trait, pack, set and stats (cost, INT, ATK, DEF, HP).
- **Watermarked card images**, uploaded once to Telegram and cached by `file_id` — repeated lookups reuse the cached upload instead of re-sending the image.
- **Hot data updates**: an admin-only `/actualizar` command pulls fresh card/translation data from two external repositories and reloads it into the running process, no restart needed. See [Architecture](#architecture) for how that pipeline works.

## Commands

| Command | Who | Description |
|---|---|---|
| `@InfoCartas_bot <name or code>` | Anyone | Inline query — live dropdown of matching cards as you type, from any chat |
| `/buscar filtro=valor, ...` | Anyone | Filtered search, e.g. `/buscar set=SP//dr, tipo=evento, coste=2` |
| `/ayuda` | Anyone | Shows the command list |
| `/mi_id` | Anyone | Shows your Telegram user ID (useful to add yourself as admin) |
| `/actualizar` | Admin | Pulls fresh data from the external repos and hot-reloads it, no restart |
| `/check_update` | Admin | Checks for pending remote changes without applying them |
| `/estado_update` | Admin | Reports whether a hot update is currently running |

Admins are configured via the `BOT_ADMIN_IDS` env var and/or `bot/admin_ids.txt` (see [`.env.example`](.env.example)).

## Architecture

InfoCartasBot doesn't own or hand-translate its card data — it builds it from two independently-maintained external repositories (the canonical English MarvelCDB dataset and a community Spanish translation project) through an offline pipeline, then serves the result over Telegram with in-memory caching.

Full breakdown — data flow diagram, why the external repos are needed, the hot-update mechanism, and the image-caching strategy — is in **[ARCHITECTURE.md](ARCHITECTURE.md)**.

## Project layout

```
bot/                    Live Telegram bot
├── main.py             Entry point: handlers, fuzzy search index, hot reload
├── data/                Runtime data (card JSON) + image tooling
│   ├── marvelcdb_spanish.json   Built card dataset (ships pre-built, see Architecture)
│   ├── marca_agua_v2.py         Watermarks card images
│   └── telegram_imagenes.py     Uploads images to Telegram, caches file_ids
└── live_update/
    └── soft_updater.py Hot in-place update: git pull + rebuild + reload, no restart

pipeline/                Offline data-prep pipeline (builds bot/data/marvelcdb_spanish.json
                         from the two external repos — see ARCHITECTURE.md)

docs/                    Additional documentation
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

- **Python** + [`python-telegram-bot`](https://github.com/python-telegram-bot/python-telegram-bot) (async, `Application`/handler-based)
- [`rapidfuzz`](https://github.com/rapidfuzz/RapidFuzz) — fuzzy name matching
- [`cachetools`](https://github.com/tkem/cachetools) — TTL in-memory caching
- [`Pillow`](https://python-pillow.org/) — image watermarking
- `python-dotenv` — local env var loading

## Highlights

A few implementation details that came up while building this:

- **Normalized fuzzy search index**: card names are pre-normalized (lowercased, accents stripped) at load time and re-indexed on every hot reload, so search stays fast and typo-tolerant without re-normalizing on every query.
- **`file_id` caching to respect Telegram's upload limits**: each watermarked image is uploaded once and its Telegram `file_id` is cached; every subsequent inline result reuses that ID instead of re-uploading, avoiding Telegram's flood limits at scale (thousands of cards).
- **Hot reload without downtime**: `/actualizar` re-pulls data and swaps it into memory under a lock, so the bot never needs a restart (and can't be triggered twice concurrently) to pick up new card packs or translation fixes.
- **Data pipeline kept separate from the bot**: the bot never talks to the external repos directly — a dedicated `pipeline/` step owns merging/translating/reformatting, so the live process only ever deals with one pre-built JSON file.

## License

MIT — see [LICENSE](LICENSE).

---

Built by [@Emigarsan](https://github.com/Emigarsan).
