"""
Holt Wassertemperaturen fuer Letzigraben und Utoquai.
Bei Fehler wird der alte Wert behalten (temperatures.json bleibt unveraendert).
"""
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx

TZ = ZoneInfo("Europe/Zurich")
ROOT = Path(__file__).resolve().parent.parent
TEMP_FILE = ROOT / "data" / "temperatures.json"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; badi-tracker/1.0; +https://github.com/derbsq/Badi-Tracker)",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-CH,de;q=0.9",
}


def fetch_letzigraben() -> dict:
    """Scrapet badi-info.ch fuer Letzigraben-Wassertemperatur."""
    r = httpx.get(
        "https://www.badi-info.ch/_temp/zh/letzigraben.htm",
        timeout=15, follow_redirects=True, headers=HEADERS
    )
    r.raise_for_status()
    # Debug: ersten 500 Zeichen ausgeben
    print(f"  Letzigraben HTML preview: {r.text[:300]!r}")
    # Robuster Regex: Zahl mit optionalem Dezimalpunkt zwischen Tags oder als Text
    m = re.search(r"(\d+[.,]\d+|\d+)\s*°?\s*C", r.text)
    if not m:
        # Fallback: suche nach bold-Zahl
        m = re.search(r"<b[^>]*>([\d.,]+)</b>|<strong[^>]*>([\d.,]+)</strong>", r.text, re.IGNORECASE)
        val = m.group(1) or m.group(2) if m else None
    else:
        val = m.group(1).replace(",", ".")
    ts = re.search(r"(Mo|Di|Mi|Do|Fr|Sa|So)[.,\s]*([\d.]+)\s*um\s*([\d:]+)", r.text)
    return {
        "temp": float(val) if val else None,
        "unit": "C",
        "source": "badi-info.ch",
        "measured_at": ts.group(0) if ts else None,
    }


def fetch_utoquai_ogd() -> dict:
    """
    Holt Zuerichsee-Wassertemperatur via OGD Stadt Zuerich CKAN-API.
    Datensatz: sid_wapo_wetterstationen, Ressource tiefenbrunnen.
    """
    # Resource-ID fuer Tiefenbrunnen im OGD-Katalog
    url = (
        "https://data.stadt-zuerich.ch/api/3/action/datastore_search_sql"
        "?sql=SELECT%20timestamp_utc,water_temperature%20FROM%20"
        "%22sid_wapo_wetterstationen_tiefenbrunnen%22%20"
        "WHERE%20water_temperature%20IS%20NOT%20NULL%20"
        "ORDER%20BY%20timestamp_utc%20DESC%20LIMIT%201"
    )
    r = httpx.get(url, timeout=15, follow_redirects=True, headers=HEADERS)
    r.raise_for_status()
    data = r.json()
    print(f"  OGD response: {json.dumps(data)[:300]}")
    records = data.get("result", {}).get("records", [])
    if records:
        return {
            "temp": round(float(records[0]["water_temperature"]), 1),
            "unit": "C",
            "source": "OGD Stadt Zürich / Wasserschutzpolizei Tiefenbrunnen",
            "measured_at": records[0].get("timestamp_utc"),
        }
    return {"temp": None, "unit": "C", "source": "OGD Stadt Zürich", "measured_at": None}


def fetch_utoquai_badoinfo() -> dict:
    """Fallback: badi-info.ch fuer Zuerichsee Tiefenbrunnen."""
    r = httpx.get(
        "https://www.badi-info.ch/_temp/zuerichsee-tiefenbrunnen.htm",
        timeout=15, follow_redirects=True, headers=HEADERS
    )
    r.raise_for_status()
    print(f"  Utoquai badi-info HTML preview: {r.text[:300]!r}")
    m = re.search(r"<strong>([\d.]+)</strong>", r.text)
    ts = re.search(r"(Mo|Di|Mi|Do|Fr|Sa|So)[.,\s]*([\d.]+)\s*um\s*([\d:]+)", r.text)
    return {
        "temp": float(m.group(1)) if m else None,
        "unit": "C",
        "source": "badi-info.ch / Zürichsee Tiefenbrunnen",
        "measured_at": ts.group(0) if ts else None,
    }


def main():
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    now_local = datetime.now(TZ).strftime("%H:%M")

    result = {
        "updated_at": now,
        "updated_at_local": now_local,
        "city": {
            "temp": 28.0,
            "unit": "C",
            "source": "static",
            "note": "Schwimmerbecken 28°C / Nichtschwimmer 32°C",
        },
        "letzigraben": {"temp": None},
        "utoquai": {"temp": None},
    }

    # Letzigraben
    try:
        result["letzigraben"] = fetch_letzigraben()
        print(f"OK Letzigraben: {result['letzigraben']['temp']}°C")
    except Exception as e:
        print(f"WARN Letzigraben: {type(e).__name__}: {e}")
        result["letzigraben"] = {"temp": None, "error": str(e)}

    # Utoquai: OGD zuerst, dann badi-info als Fallback
    try:
        result["utoquai"] = fetch_utoquai_ogd()
        if result["utoquai"]["temp"] is None:
            raise ValueError("OGD lieferte None, versuche Fallback")
        print(f"OK Utoquai (OGD): {result['utoquai']['temp']}°C")
    except Exception as e:
        print(f"WARN Utoquai OGD: {e}, versuche badi-info Fallback")
        try:
            result["utoquai"] = fetch_utoquai_badoinfo()
            print(f"OK Utoquai (badi-info): {result['utoquai']['temp']}°C")
        except Exception as e2:
            print(f"WARN Utoquai badi-info: {e2}")
            result["utoquai"] = {"temp": None, "error": str(e2)}

    TEMP_FILE.parent.mkdir(parents=True, exist_ok=True)
    TEMP_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"OK temperatures.json geschrieben ({now_local} CH-Zeit)")


if __name__ == "__main__":
    main()
