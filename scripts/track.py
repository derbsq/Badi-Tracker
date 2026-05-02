"""
Badi-Tracker: Erfasst alle 10 Min die Auslastung der Schweizer Baeder
via Crowdmonitor-WebSocket und reichert mit Wetter, Ferien/Feiertagen
und Wassertemperaturen an.
"""
import asyncio
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import websockets
import httpx

# --- Konfiguration ---
WS_URL = "wss://badi-public.crowdmonitor.ch:9591/api"
SUBSCRIBE_CMD = "1 all"
TIMEOUT_SECONDS = 15
TZ = ZoneInfo("Europe/Zurich")

# Nur diese Bäder in der CSV speichern (UIDs aus dem WebSocket-Feed)
TRACKED_UIDS = {
    "flb6939",   # Flussbad Oberer Letten
    "flb6940",   # Flussbad Unterer Letten
    "fb012",     # Freibad Heuried
    "LETZI-1",   # Freibad Letzigraben
    "SSD-11",    # Freibad Seebach
    "SSD-4",     # Hallenbad City
    "SSD-7",     # Hallenbad Oerlikon
    "SSD-10",    # Seebad Utoquai
}

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
RAW_FILE = DATA_DIR / "auslastung.csv"
WEATHER_FILE = DATA_DIR / "weather.csv"
TEMP_FILE = DATA_DIR / "temperatures.json"

ZURICH_LAT = 47.3739
ZURICH_LON = 8.5364


# ---- WebSocket ----

async def fetch_snapshot() -> list[dict]:
    async with websockets.connect(WS_URL, open_timeout=10, close_timeout=5) as ws:
        await ws.send(SUBSCRIBE_CMD)
        raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT_SECONDS)
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(f"Unerwartetes Datenformat: {type(data)}")
        return data


# ---- Wetter ----

def fetch_weather() -> dict:
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


# ---- Wassertemperaturen ----

def fetch_temperatures() -> dict:
    """
    City:       konstant 28C (Schwimmerbecken), kein Feed noetig
    Letzigraben: badi-info.ch/_temp/zh/letzigraben.htm  (HTML-Scraping)
    Utoquai:    Tecdottir-API der Wasserschutzpolizei Zuerich
                (Messstation Tiefenbrunnen, OGD Stadt Zuerich)
    """
    temps = {
        "city": {
            "temp": 28.0,
            "unit": "C",
            "source": "static",
            "note": "Schwimmerbecken 28°C / Nichtschwimmer 32°C",
            "updated_at": None,
        },
        "letzigraben": {
            "temp": None,
            "unit": "C",
            "source": "badi-info.ch",
            "note": None,
            "updated_at": None,
        },
        "utoquai": {
            "temp": None,
            "unit": "C",
            "source": "tecdottir/tiefenbrunnen",
            "note": "Zürichsee-Temperatur Messstation Tiefenbrunnen",
            "updated_at": None,
        },
    }

    headers = {"User-Agent": "badi-tracker/1.0 (github.com/derbsq/Badi-Tracker)"}

    # Letzigraben
    try:
        r = httpx.get(
            "https://www.badi-info.ch/_temp/zh/letzigraben.htm",
            timeout=10, follow_redirects=True, headers=headers
        )
        r.raise_for_status()
        m = re.search(r"<strong>([\d.]+)</strong>", r.text)
        if m:
            temps["letzigraben"]["temp"] = float(m.group(1))
            # Zeitstempel aus Text holen, z.B. "Sa, 02.05. um 06:48"
            ts = re.search(r"(Mo|Di|Mi|Do|Fr|Sa|So),\s*([\d.]+)\s*um\s*([\d:]+)", r.text)
            temps["letzigraben"]["updated_at"] = ts.group(0) if ts else None
    except Exception as e:
        print(f"WARN Letzigraben-Temp: {e}")

    # Utoquai via Tecdottir
    try:
        today = datetime.now(TZ).strftime("%Y-%m-%d")
        r = httpx.get(
            f"https://tecdottir.herokuapp.com/measurements/tiefenbrunnen"
            f"?startDate={today}&endDate={today}",
            timeout=15, follow_redirects=True, headers=headers
        )
        r.raise_for_status()
        results = r.json().get("result", [])
        for entry in reversed(results):
            wt = (entry.get("values") or {}).get("water_temperature", {}).get("value")
            if wt is not None:
                temps["utoquai"]["temp"] = round(float(wt), 1)
                temps["utoquai"]["updated_at"] = entry.get("timestamp_utc")
                break
    except Exception as e:
        print(f"WARN Utoquai-Temp (Tecdottir): {e}")

    return temps


# ---- Feiertage / Schulferien ----

def is_swiss_holiday(d: datetime) -> bool:
    try:
        import holidays
        ch_zh = holidays.country_holidays("CH", subdiv="ZH")
        return d.date() in ch_zh
    except Exception:
        return False


def is_zh_school_holiday(d: datetime) -> bool:
    ferien = [
        ("2026-04-25", "2026-05-10"),
        ("2026-07-11", "2026-08-16"),
        ("2026-10-03", "2026-10-18"),
        ("2026-12-19", "2027-01-03"),
        ("2027-02-06", "2027-02-21"),
        ("2027-04-17", "2027-05-02"),
    ]
    today = d.date().isoformat()
    return any(s <= today <= e for s, e in ferien)


# ---- CSV Helper ----

def append_csv(path: Path, fieldnames: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    new_file = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if new_file:
            writer.writeheader()
        writer.writerows(rows)


# ---- Main ----

def main() -> None:
    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(TZ)
    timestamp_iso = now_utc.isoformat(timespec="seconds")

    # 1. Auslastung
    try:
        snapshot = asyncio.run(fetch_snapshot())
    except Exception as e:
        print(f"FEHLER beim WebSocket-Fetch: {e}")
        raise

    context = {
        "timestamp_utc": timestamp_iso,
        "timestamp_local": now_local.isoformat(timespec="seconds"),
        "weekday": now_local.strftime("%A"),
        "hour": now_local.hour,
        "minute": now_local.minute,
        "is_holiday": is_swiss_holiday(now_local),
        "is_school_holiday": is_zh_school_holiday(now_local),
    }

    rows = []
    for entry in snapshot:
        if entry.get("uid") not in TRACKED_UIDS:
            continue
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

    # 2. Wetter
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
        append_csv(WEATHER_FILE, list(weather_row.keys()), [weather_row])
        print(f"OK Wetter: {weather.get('temperature_2m')}C, "
              f"Niederschlag {weather.get('precipitation')}mm")
    except Exception as e:
        print(f"WARN Wetter: {e}")

    # 3. Wassertemperaturen
    try:
        temps = fetch_temperatures()
        temps["updated_at"] = timestamp_iso
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        TEMP_FILE.write_text(json.dumps(temps, indent=2, ensure_ascii=False))
        print(
            f"OK Temperaturen: "
            f"City {temps['city']['temp']}C, "
            f"Letzi {temps['letzigraben']['temp']}C, "
            f"Uto {temps['utoquai']['temp']}C"
        )
    except Exception as e:
        print(f"WARN Temperaturen: {e}")


if __name__ == "__main__":
    main()
