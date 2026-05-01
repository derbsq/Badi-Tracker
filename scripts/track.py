"""
Badi-Tracker: Erfasst alle 10 Min die Auslastung der Schweizer Baeder
via Crowdmonitor-WebSocket und reichert mit Wetter & Ferien/Feiertagen an.
"""
import asyncio
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import websockets
import httpx

# --- Konfiguration ---
WS_URL = "wss://badi-public.crowdmonitor.ch:9591/api"
SUBSCRIBE_CMD = "1 all"
TIMEOUT_SECONDS = 15  # Wie lange auf erste Message warten
TZ = ZoneInfo("Europe/Zurich")

# Datenpfade (relativ zum Repo-Root)
ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_FILE = DATA_DIR / "auslastung.csv"
WEATHER_FILE = DATA_DIR / "weather.csv"

# Zuerich-Koordinaten fuers Wetter (Hallenbad City)
ZURICH_LAT = 47.3739
ZURICH_LON = 8.5364


async def fetch_snapshot() -> list[dict]:
    """Verbindet sich, schickt Subscribe, sammelt eine Message, schliesst Verbindung."""
    async with websockets.connect(WS_URL, open_timeout=10, close_timeout=5) as ws:
        await ws.send(SUBSCRIBE_CMD)
        # Erste eingehende Nachricht enthaelt das vollstaendige Array aller Baeder
        raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT_SECONDS)
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(f"Unerwartetes Datenformat: {type(data)}")
        return data


def fetch_weather() -> dict:
    """Holt aktuelles Wetter fuer Zuerich von Open-Meteo (gratis, kein Key)."""
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={ZURICH_LAT}&longitude={ZURICH_LON}"
        "&current=temperature_2m,relative_humidity_2m,precipitation,"
        "weather_code,cloud_cover,wind_speed_10m"
        "&timezone=Europe%2FZurich"
    )
    r = httpx.get(url, timeout=15)
    r.raise_for_status()
    return r.json().get("current", {})


def is_swiss_holiday(d: datetime) -> bool:
    """Pruefe Schweizer Feiertage (Bund + ZH) fuers gegebene Datum."""
    # Minimaler Set fester ZH-Feiertage; Ostern etc. werden via 'holidays' lib gemacht
    try:
        import holidays
        ch_zh = holidays.country_holidays("CH", subdiv="ZH")
        return d.date() in ch_zh
    except Exception:
        return False


def is_zh_school_holiday(d: datetime) -> bool:
    """
    Zuercher Schulferien (vereinfacht, manuell gepflegt).
    Quelle: stadt-zuerich.ch/schulferien - update jaehrlich.
    Format: Liste von (start, end) Tupeln im Format YYYY-MM-DD inklusiv.
    """
    ferien_2026 = [
        ("2026-04-25", "2026-05-10"),  # Fruehlingsferien 2026
        ("2026-07-11", "2026-08-16"),  # Sommerferien 2026
        ("2026-10-03", "2026-10-18"),  # Herbstferien 2026
        ("2026-12-19", "2027-01-03"),  # Weihnachtsferien 2026/27
    ]
    ferien_2027 = [
        ("2027-02-06", "2027-02-21"),  # Sportferien 2027
        ("2027-04-17", "2027-05-02"),  # Fruehlingsferien 2027
    ]
    today = d.date()
    for start, end in ferien_2026 + ferien_2027:
        s = datetime.fromisoformat(start).date()
        e = datetime.fromisoformat(end).date()
        if s <= today <= e:
            return True
    return False


def append_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    """Haengt Zeilen an CSV an, erzeugt Header beim ersten Mal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(TZ)
    timestamp_iso = now_utc.isoformat(timespec="seconds")

    # 1. Auslastungsdaten holen
    try:
        snapshot = asyncio.run(fetch_snapshot())
    except Exception as e:
        print(f"FEHLER beim WebSocket-Fetch: {e}")
        raise

    # 2. Kontext berechnen (gilt fuer alle Zeilen)
    context = {
        "timestamp_utc": timestamp_iso,
        "timestamp_local": now_local.isoformat(timespec="seconds"),
        "weekday": now_local.strftime("%A"),
        "hour": now_local.hour,
        "minute": now_local.minute,
        "is_holiday": is_swiss_holiday(now_local),
        "is_school_holiday": is_zh_school_holiday(now_local),
    }

    # 3. Auslastungs-Zeilen bauen (eine pro Bad)
    rows = []
    for entry in snapshot:
        try:
            currentfill = int(entry.get("currentfill", 0) or 0)
            maxspace = int(entry.get("maxspace", 0) or 0)
            freespace = int(entry.get("freespace", 0) or 0)
        except (ValueError, TypeError):
            currentfill = maxspace = freespace = 0

        ratio = (currentfill / maxspace) if maxspace > 0 else 0.0
        rows.append({
            **context,
            "uid": entry.get("uid", ""),
            "name": entry.get("name", ""),
            "currentfill": currentfill,
            "freespace": freespace,
            "maxspace": maxspace,
            "fill_ratio": round(ratio, 4),
        })

    auslastung_fields = [
        "timestamp_utc", "timestamp_local", "weekday", "hour", "minute",
        "is_holiday", "is_school_holiday",
        "uid", "name", "currentfill", "freespace", "maxspace", "fill_ratio",
    ]
    append_csv(RAW_FILE, auslastung_fields, rows)
    print(f"OK Auslastung: {len(rows)} Eintraege geschrieben")

    # 4. Wetter (separater File - eine Zeile pro Snapshot)
    try:
        weather = fetch_weather()
        weather_row = {
            "timestamp_utc": timestamp_iso,
            "timestamp_local": now_local.isoformat(timespec="seconds"),
            "temperature_c": weather.get("temperature_2m"),
            "humidity_pct": weather.get("relative_humidity_2m"),
            "precipitation_mm": weather.get("precipitation"),
            "weather_code": weather.get("weather_code"),
            "cloud_cover_pct": weather.get("cloud_cover"),
            "wind_speed_kmh": weather.get("wind_speed_10m"),
        }
        weather_fields = list(weather_row.keys())
        append_csv(WEATHER_FILE, weather_fields, [weather_row])
        print(f"OK Wetter: {weather.get('temperature_2m')}C, "
              f"Niederschlag {weather.get('precipitation')}mm")
    except Exception as e:
        # Wetter-Fehler soll nicht den ganzen Run kippen
        print(f"WARN Wetter konnte nicht geladen werden: {e}")


if __name__ == "__main__":
    main()
