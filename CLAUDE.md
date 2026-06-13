# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo does

Automated data pipeline: every 10 minutes a GitHub Actions workflow runs `scripts/track.py`, pulls occupancy data from the Crowdmonitor WebSocket, weather from Open-Meteo, and commits the results directly back into `data/` via the GitHub Contents API. No separate deployment — the repo itself is the data store.

The companion repo `derbsq/training-hub` reads this data via raw GitHub URLs.

## Running locally

```bash
pip install -r requirements.txt          # websockets, httpx, holidays

# Requires GITHUB_TOKEN with repo write access:
export GITHUB_TOKEN=ghp_...
python scripts/track.py                  # full run: occupancy + weather + temps

python scripts/fetch_temps.py            # water temps only (writes local file, no GitHub commit)
```

`fetch_temps.py` writes to `data/temperatures.json` on disk. `track.py` commits everything to GitHub directly — running it locally will produce a real commit.

## Architecture

### Scripts

| Script | Trigger | What it does |
|---|---|---|
| `scripts/track.py` | Every 10 min (06:00–22:00 CH) | Occupancy + weather snapshot → appends to CSVs, updates JSON files, commits via GitHub API |
| `scripts/fetch_temps.py` | 3×/day (08:00, 12:00, 18:00 CH) | Water temps only → writes local `temperatures.json` |

`track.py` is the main script and subsumes the temp logic from `fetch_temps.py` — both exist because `fetch_temps.py` was the standalone predecessor.

### GitHub API write pattern

`track.py` never uses `git` directly. It uses the GitHub Contents API (`gh_get_file` / `gh_put_file`) to read-modify-write files atomically. For files >1MB (the CSV), it falls back to the Git Blob API. The `GITHUB_TOKEN` secret is injected by the Actions workflow.

### Data files

| File | Updated | Content |
|---|---|---|
| `data/auslastung.csv` | Every 10 min | One row per tracked Badi per snapshot. ~29k+ rows, grows ~4300 rows/day |
| `data/weather.csv` | Every 10 min | One Zürich weather row per snapshot |
| `data/temperatures.json` | Every 30 min (within track.py), 3×/day (fetch_temps.py) | Current water temps for City, Letzigraben, Utoquai, Letten |
| `data/weather_forecast.json` | Every 30 min | 7-day forecast with hourly breakdown |
| `data/bahnen_city.json` | Manual | Lane config for Hallenbad City Schwimmerbecken (6 lanes) |
| `data/vario_city.json` | Manual | Lane config for Hallenbad City Variobecken (4 lanes) |

### Tracked pools (`TRACKED_UIDS` in track.py)

`flb6939` (Ob. Letten), `flb6940` (Un. Letten), `fb012` (Heuried), `LETZI-1` (Letzigraben), `SSD-4` (Hallenbad City), `SSD-7`, `SSD-10` (Utoquai), `SSD-11`. The WebSocket returns all Swiss pools; filtering happens on `uid`.

### Water temperature sources

- **Letzigraben**: scraped from `badi-info.ch` (HTML scraping, brittle — multiple regex fallbacks)
- **Utoquai**: OGD Stadt Zürich CSV via `Range: bytes=-5000` to avoid downloading the full historical file (since 2007)
- **Letten**: `hydroproweb.zh.ch` (primary), falls back to `api.existenz.ch` stations 2135/2030/2011
- **Hallenbad City**: static 28°C (pool temp doesn't change)

### GitHub Actions workflows

| Workflow | Schedule | Notes |
|---|---|---|
| `track.yml` | Every 10 min, 24/7 | Skips runs outside 06:00–22:00 CH time via a pre-check step |
| `temps_workflow.yml` | 3×/day | Standalone temps fetch |
| `cleanup_workflow` | `workflow_dispatch` only | One-shot: filter CSV to only `TRACKED_UIDS` (run manually if CSV contains stray rows) |

Push conflicts are handled with `git pull --rebase` + 3 retries (5s apart) in the workflow shell step — necessary because concurrent runs can race.

### Maintenance

- **School holiday dates** are hardcoded in `track.py` (`is_zh_school_holiday`, `ferien` list). Update annually.
- **Swiss public holidays** use the `holidays` library (`holidays.country_holidays("CH", subdiv="ZH")`).
