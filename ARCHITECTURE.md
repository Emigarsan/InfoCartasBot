# Architecture

## Overview

InfoCartasBot is a Telegram bot that serves Marvel Champions LCG card data in Spanish. It does **not** own or translate that data itself — it consumes it from two external, independently-maintained repositories and packages the result for fast in-memory lookup.

```
                          ┌─────────────────────────────┐
                          │   marvelsdb-json-data repo   │  canonical English card
                          │   (external, upstream)       │  data, one JSON per pack
                          └───────────────┬──────────────┘
                                          │
┌─────────────────────────┐              │  pipeline/json_traducido.py
│      freakmod repo       │  Spanish     │  merges by card "code"
│      (external)          │  translations│
└───────────────┬──────────┘              │
                │                         │
                └─────────────►───────────┘
                                          │
                              pipeline/updated_json/*.json  (per-pack, merged)
                                          │
                              pipeline/merge_json.py
                              (combines packs + rewrites MarvelCDB
                               markup into Telegram Markdown/emoji)
                                          │
                              bot/data/marvelcdb_spanish.json
                                          │
                              bot/main.py  ──►  Telegram inline queries
```

## Why external repos

- **`marvelsdb-json-data`**: the canonical, community-maintained English card database used by MarvelCDB itself. InfoCartasBot doesn't fork or duplicate this data by hand — it pulls it directly so card data stays accurate as new packs release.
- **`freakmod`**: a community repository of Spanish translations for Marvel Champions cards, keyed by the same card `code` used in `marvelsdb-json-data`.

Neither repo is vendored into this repository. They're expected to be cloned as siblings of this project:

```
your-workspace/
├── InfoCartasBot/
│   └── pipeline/marvelsdb-json-data/   ← clone marvelsdb-json-data here
├── freakmod/                            ← clone freakmod here
```

Both paths are gitignored in this repo (`/pipeline/marvelsdb-json-data/`, `/freakmod/`) since their content belongs to those upstream projects, not to InfoCartasBot.

## The data pipeline (`pipeline/`)

1. **`pipeline/json_traducido.py`** — for each pack, merges the English card JSON from `marvelsdb-json-data/pack/*.json` with the matching Spanish translation from `freakmod/translations/es/pack/*.json`, keyed by card `code`. Writes one merged file per pack to `pipeline/updated_json/`.
2. **`pipeline/merge_json.py`** — combines every file in `pipeline/updated_json/` into a single `marvelcdb_spanish.json`, and rewrites MarvelCDB's markup (`<b>`, `[[...]]`, `[physical]`, etc.) into Telegram-flavored Markdown and emoji.
3. The result gets copied into `bot/data/marvelcdb_spanish.json`, which `bot/main.py` loads at startup.

`bot/data/` already ships a pre-built `marvelcdb_spanish.json`, so the bot runs standalone without the pipeline. The pipeline is only needed to *regenerate* that file after upstream card/translation updates.

Both `MARVELCDB_REPO_DIR` and `TRANSLATION_REPO_DIR` env vars (see `.env.example`) let you point `pipeline/json_traducido.py` at repo clones in a different location; by default it looks for them as siblings, per the layout above.

## Triggering an update

Updates are pulled with an **in-bot hot update** (`bot/live_update/soft_updater.py`), triggered by the `/actualizar` Telegram admin command: it runs `git pull` on the two external repos directly on the server, re-runs the pipeline, regenerates watermarked images, and reloads the new data into the running bot process without a restart.

## Image watermarking and caching

Card images are watermarked (`bot/data/marca_agua_v2.py`) before being served, then uploaded once to a Telegram chat via `bot/data/telegram_imagenes.py` to obtain a Telegram `file_id`. Subsequent inline queries reuse that cached `file_id` (`bot/data/IC_file_id_cache.json`) instead of re-uploading the image, which is both faster and avoids Telegram's upload rate limits. Cards whose watermarking fails are recorded in a local `failed_cards.json` so they aren't retried on every run — see [docs/failed-cards-tracking.md](docs/failed-cards-tracking.md).
