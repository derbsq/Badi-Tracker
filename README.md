# Badi-Tracker Zürich

Trackt alle 10 Minuten die Auslastung der Schweizer Bäder via Crowdmonitor-WebSocket
(`wss://badi-public.crowdmonitor.ch:9591/api`) und reichert die Daten mit Wetter
(Open-Meteo) und Schweizer Feiertagen / Zürcher Schulferien an.

## Setup

### 1. Repo erstellen

1. Auf [github.com](https://github.com) einloggen → "New repository"
2. Name: z.B. `badi-tracker`
3. **Public** wählen (sonst Action-Limits)
4. "Create repository"

### 2. Files hochladen

Entweder per Web-Upload (bei "uploading an existing file") oder per Terminal:

```bash
git clone https://github.com/DEIN_USER/badi-tracker.git
cd badi-tracker
# Files aus diesem Paket reinkopieren
git add .
git commit -m "Initial setup"
git push
```

### 3. Actions aktivieren

1. Im Repo → Tab "Actions" → falls nötig "I understand my workflows, go ahead and enable them"
2. Workflow "Track Badi Auslastung" sollte erscheinen
3. Auf "Run workflow" klicken (manuelle Test-Ausführung)
4. Falls grün → läuft danach automatisch alle 10 Minuten

### 4. Lokal testen (optional)

```bash
pip install -r requirements.txt
python scripts/track.py
cat data/auslastung.csv
```

## Datenstruktur

`data/auslastung.csv` — eine Zeile pro Bad pro Snapshot:

| Spalte | Bedeutung |
|--------|-----------|
| `timestamp_utc` / `timestamp_local` | Zeitpunkt der Messung |
| `weekday`, `hour`, `minute` | Zeit-Dimensionen für Auswertung |
| `is_holiday` | Schweizer Feiertag (CH+ZH) |
| `is_school_holiday` | Zürcher Schulferien |
| `uid` / `name` | Bad-Identifikation |
| `currentfill` | Aktuelle Anzahl Personen |
| `freespace` / `maxspace` | Freie Plätze / Kapazität |
| `fill_ratio` | currentfill / maxspace (0.0–1.0) |

`data/weather.csv` — eine Zeile pro Snapshot mit Zürcher Wetterdaten.

Join geht später über `timestamp_utc` (auf 10-Min-Buckets gerundet).

## Hinweise

- **Schulferien-Daten** sind in `scripts/track.py` hardcoded und müssen jährlich
  aktualisiert werden (Konstanten `ferien_2026`, `ferien_2027` etc.).
- **GitHub-Cron ist nicht minutengenau** — Läufe können um ein paar Minuten
  verschoben sein (besonders zur vollen Stunde, wenn viele Repos ihre Cronjobs
  starten). Für Auswertungszwecke irrelevant.
- **Datenmenge**: ~30 Bäder × 144 Snapshots/Tag = ~4'300 Zeilen/Tag,
  ca. 0.5–1 MB/Monat unkomprimiert. CSV bleibt jahrelang handlich.

## Betroffene Bäder filtern

Der Endpoint liefert alle Schweizer Bäder. Beim Auswerten einfach filtern:

```python
import pandas as pd
df = pd.read_csv("data/auslastung.csv")
city = df[df["uid"] == "SSD-4"]   # Hallenbad City
letzi = df[df["name"].str.contains("Letzigraben", case=False)]
```
