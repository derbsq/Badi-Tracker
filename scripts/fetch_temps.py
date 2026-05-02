"""
Holt Wassertemperaturen fuer Letzigraben und Utoquai
und schreibt sie in data/temperatures.json.
Wird 3x taeglich via GitHub Action ausgefuehrt (08:00, 12:00, 18:00 CH-Zeit).
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
HEADERS = {"User-Agent": "badi-tracker/1.0 (github.com/derbsq/Badi-Tracker)"}


def fetch_letzigraben() -> dict:
    r = httpx.get(
        "https://www.badi-info.ch/_temp/zh/letzigraben.htm",
        timeout=10, follow_redirects=True, headers=HEADERS
    )
    r.raise_for_status()
    m = re.search(r"<strong>([\d.]+)</strong>", r.text)
    ts = re.search(r"(Mo|Di|Mi|Do|Fr|Sa|So),\s*([\d.]+)\s*um\s*([\d:]+)", r.text)
    return {
        "temp": float(m.group(1)) if m else None,
        "unit": "C",
        "source": "badi-info.ch",
        "measured_at": ts.group(0) if ts else None,
    }


def fetch_utoquai() -> dict:
    today = datetime.now(TZ).strftime("%Y-%m-%d")
    r = httpx.get(
        f"https://tecdottir.herokuapp.com/measurements/tiefenbrunnen"
        f"?startDate={today}&endDate={today}",
        timeout=15, follow_redirects=True, headers=HEADERS
    )
    r.raise_for_status()
    results = r.json().get("result", [])
    for entry in reversed(results):
        wt = (entry.get("values") or {}).get("water_temperature", {}).get("value")
        if wt is not None:
            return {
                "temp": round(float(wt), 1),
                "unit": "C",
                "source": "tecdottir/tiefenbrunnen",
                "note": "Zürichsee, Messstation Tiefenbrunnen",
                "measured_at": entry.get("timestamp_utc"),
            }
    return {"temp": None, "unit": "C", "source": "tecdottir/tiefenbrunnen", "measured_at": None}


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
        "letzigraben": {},
        "utoquai": {},
    }

    try:
        result["letzigraben"] = fetch_letzigraben()
        print(f"OK Letzigraben: {result['letzigraben']['temp']}°C")
    except Exception as e:
        result["letzigraben"] = {"temp": None, "error": str(e)}
        print(f"WARN Letzigraben: {e}")

    try:
        result["utoquai"] = fetch_utoquai()
        print(f"OK Utoquai: {result['utoquai']['temp']}°C")
    except Exception as e:
        result["utoquai"] = {"temp": None, "error": str(e)}
        print(f"WARN Utoquai: {e}")

    TEMP_FILE.parent.mkdir(parents=True, exist_ok=True)
    TEMP_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"OK temperatures.json geschrieben ({now_local} CH-Zeit)")


if __name__ == "__main__":
    main()
