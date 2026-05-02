"""
Holt Wassertemperaturen fuer Letzigraben und Utoquai (Zürichsee).

Quellen:
- Letzigraben: badi-info.ch scraping
- Utoquai: OGD Stadt Zürich CSV (Messstation Tiefenbrunnen, Wasserschutzpolizei)
"""
import csv
import io
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
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*;q=0.8",
}


def fetch_letzigraben() -> dict:
    print("  Fetching Letzigraben (badi-info.ch)...", flush=True)
    r = httpx.get(
        "https://www.badi-info.ch/_temp/zh/letzigraben.htm",
        timeout=15, follow_redirects=True, headers=HEADERS
    )
    r.raise_for_status()
    print(f"  Status: {r.status_code}, Encoding: {r.encoding}", flush=True)

    # Mehrere Patterns versuchen
    for pattern in [
        r"<strong[^>]*>\s*([\d.,]+)\s*</strong>",
        r"<b[^>]*>\s*([\d.,]+)\s*</b>",
        r"Becken[^<]*<[^>]+>\s*([\d.,]+)",
        r"(1[0-9]|2[0-9])\.([\d])\s*°",
    ]:
        m = re.search(pattern, r.text, re.IGNORECASE)
        if m:
            raw = m.group(1).replace(",", ".")
            print(f"  Match: '{raw}' via pattern: {pattern}", flush=True)
            # Messzeit extrahieren
            ts = re.search(r"(Mo|Di|Mi|Do|Fr|Sa|So)[^<]{0,5}[\d]{2}\.[\d]{2}[^<]{0,10}um\s*([\d:]+)", r.text)
            measured_at = ts.group(0).strip() if ts else None
            return {
                "temp": float(raw),
                "unit": "C",
                "source": "badi-info.ch",
                "measured_at": measured_at,
            }

    # Letzter Versuch: alle Zahlen im Text
    numbers = re.findall(r"\b(\d{1,2}[.,]\d)\b", r.text)
    print(f"  All numbers found in text: {numbers}", flush=True)
    print(f"  Full HTML (first 800 chars): {r.text[:800]!r}", flush=True)
    return {"temp": None, "unit": "C", "source": "badi-info.ch"}


def fetch_utoquai() -> dict:
    """
    Holt letzten Messwert von Tiefenbrunnen via OGD CSV der Stadt Zürich.
    Die CSV ist gross (seit 2007), daher lesen wir nur die letzten Bytes.
    """
    print("  Fetching Utoquai (OGD Tiefenbrunnen CSV)...", flush=True)
    url = "https://data.stadt-zuerich.ch/dataset/sid_wapo_wetterstationen/download/messwerte_tiefenbrunnen_seit2007-heute.csv"

    # Hole nur letzten Teil der Datei (letzte ~5KB reichen fuer mehrere Zeilen)
    r = httpx.get(
        url, timeout=30, follow_redirects=True,
        headers={**HEADERS, "Range": "bytes=-5000"}
    )
    print(f"  Status: {r.status_code}, Content-Length: {len(r.content)}", flush=True)

    if r.status_code not in (200, 206):
        r.raise_for_status()

    # CSV parsen - letzte vollstaendige Zeile mit water_temperature finden
    text = r.text
    # Falls kein Header (206 Partial Content), Header separat holen
    if r.status_code == 206:
        header_r = httpx.get(url, timeout=15, follow_redirects=True,
                             headers={**HEADERS, "Range": "bytes=0-500"})
        header_line = header_r.text.split("\n")[0]
        text = header_line + "\n" + text.lstrip()
        # Erste Zeile koennte unvollstaendig sein, entfernen
        lines = text.split("\n")
        if len(lines) > 2:
            text = lines[0] + "\n" + "\n".join(lines[2:])

    print(f"  Last 500 chars: {text[-500:]!r}", flush=True)

    reader = csv.DictReader(io.StringIO(text))
    last_row = None
    for row in reader:
        wt = row.get("water_temperature") or row.get("Wassertemperatur") or row.get("watertemperature")
        if wt and wt.strip() not in ("", "NA", "NaN", "null"):
            last_row = row
            last_wt = wt

    if last_row:
        print(f"  Headers: {list(last_row.keys())}", flush=True)
        ts = last_row.get("timestamp_utc") or last_row.get("Datum") or last_row.get("timestamp")
        return {
            "temp": round(float(last_wt.replace(",", ".")), 1),
            "unit": "C",
            "source": "OGD Stadt Zürich / Tiefenbrunnen",
            "measured_at": ts,
        }

    print("  WARNING: No water_temperature found in CSV", flush=True)
    return {"temp": None, "unit": "C", "source": "OGD Stadt Zürich"}


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
            "measured_at": None,
        },
        "letzigraben": {"temp": None},
        "utoquai": {"temp": None},
    }

    try:
        result["letzigraben"] = fetch_letzigraben()
        print(f"OK Letzigraben: {result['letzigraben']['temp']}°C "
              f"(gemessen: {result['letzigraben'].get('measured_at')})", flush=True)
    except Exception as e:
        print(f"ERROR Letzigraben: {type(e).__name__}: {e}", flush=True)
        result["letzigraben"] = {"temp": None, "error": str(e)}

    try:
        result["utoquai"] = fetch_utoquai()
        print(f"OK Utoquai: {result['utoquai']['temp']}°C "
              f"(gemessen: {result['utoquai'].get('measured_at')})", flush=True)
    except Exception as e:
        print(f"ERROR Utoquai: {type(e).__name__}: {e}", flush=True)
        result["utoquai"] = {"temp": None, "error": str(e)}

    TEMP_FILE.parent.mkdir(parents=True, exist_ok=True)
    TEMP_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False))
    print(f"OK temperatures.json geschrieben ({now_local} CH-Zeit)", flush=True)


if __name__ == "__main__":
    main()
