"""
Badi-Tracker: Laeuft auf Railway als Cron-Job alle 10 Min.
"""
import asyncio
import base64
import csv
import io
import json
import os
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

TRACKED_UIDS = {
    "flb6939", "flb6940", "fb012", "LETZI-1",
    "SSD-11", "SSD-4", "SSD-7", "SSD-10",
}

ZURICH_LAT = 47.3739
ZURICH_LON = 8.5364

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = "derbsq/Badi-Tracker"
GITHUB_BRANCH = "main"
GITHUB_API = "https://api.github.com"

HEADERS_GH = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}
HEADERS_WEB = {
    "User-Agent": "Mozilla/5.0 (compatible; badi-tracker/1.0; +https://github.com/derbsq/Badi-Tracker)",
    "Accept": "text/html,*/*;q=0.8",
}


# ---- GitHub API Helpers ----

def gh_get_file(path: str) -> tuple[str, str]:
    """
    Gibt (content_decoded, sha) zurück.
    Unterstützt Dateien >1MB via Git Blob API.
    """
    r = httpx.get(
        f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}",
        headers=HEADERS_GH, params={"ref": GITHUB_BRANCH}, timeout=15
    )
    if r.status_code == 404:
        return "", ""
    r.raise_for_status()
    data = r.json()

    sha = data.get("sha", "")

    # Datei zu gross für Contents API (>1MB) → content ist leer/null
    if not data.get("content") or data.get("content", "").strip() == "":
        print(f"  Datei {path} zu gross (>{data.get('size',0)//1024}KB), nutze Git Blob API")
        r2 = httpx.get(
            f"{GITHUB_API}/repos/{GITHUB_REPO}/git/blobs/{sha}",
            headers={**HEADERS_GH, "Accept": "application/vnd.github.raw+json"},
            timeout=60
        )
        r2.raise_for_status()
        return r2.text, sha

    content = base64.b64decode(data["content"]).decode("utf-8")
    return content, sha


def gh_put_file(path: str, content: str, sha: str, message: str) -> None:
    payload = {
        "message": message,
        "content": base64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha
    r = httpx.put(
        f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/{path}",
        headers=HEADERS_GH, json=payload, timeout=30
    )
    r.raise_for_status()


def append_to_csv_in_repo(repo_path: str, fieldnames: list[str], new_rows: list[dict]) -> None:
    existing, sha = gh_get_file(repo_path)

    if existing.strip():
        reader = csv.DictReader(io.StringIO(existing))
        rows = list(reader)
    else:
        rows = []

    rows.extend(new_rows)

    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fieldnames, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    new_content = buf.getvalue()

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
    gh_put_file(repo_path, new_content, sha, f"data: snapshot {now_str}")
    print(f"OK GitHub commit: {repo_path} ({len(new_rows)} neue Zeilen, {len(rows)} total)")


# ---- WebSocket ----

async def fetch_snapshot() -> list[dict]:
    async with websockets.connect(WS_URL, open_timeout=10, close_timeout=5) as ws:
        await ws.send(SUBSCRIBE_CMD)
        raw = await asyncio.wait_for(ws.recv(), timeout=TIMEOUT_SECONDS)
        data = json.loads(raw)
        if not isinstance(data, list):
            raise ValueError(f"Unerwartetes Format: {type(data)}")
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


def fetch_weather_forecast() -> list:
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={ZURICH_LAT}&longitude={ZURICH_LON}"
        "&daily=temperature_2m_max,temperature_2m_min,precipitation_sum,"
        "precipitation_probability_max,weather_code,wind_speed_10m_max"
        "&hourly=temperature_2m,precipitation_probability,precipitation,weather_code"
        "&forecast_days=7"
        "&timezone=Europe%2FZurich"
    )
    r = httpx.get(url, timeout=15)
    r.raise_for_status()
    data = r.json()
    daily = data.get("daily", {})
    hourly = data.get("hourly", {})
    hourly_by_date = {}
    for i, t in enumerate(hourly.get("time", [])):
        date = t[:10]
        if date not in hourly_by_date:
            hourly_by_date[date] = []
        hourly_by_date[date].append({
            "hour": int(t[11:13]),
            "temp": hourly["temperature_2m"][i] if i < len(hourly.get("temperature_2m", [])) else None,
            "precip_prob": hourly["precipitation_probability"][i] if i < len(hourly.get("precipitation_probability", [])) else None,
            "precip": hourly["precipitation"][i] if i < len(hourly.get("precipitation", [])) else None,
            "weather_code": hourly["weather_code"][i] if i < len(hourly.get("weather_code", [])) else None,
        })
    days = []
    for i, date in enumerate(daily.get("time", [])):
        days.append({
            "date": date,
            "temp_max": daily["temperature_2m_max"][i] if i < len(daily.get("temperature_2m_max", [])) else None,
            "temp_min": daily["temperature_2m_min"][i] if i < len(daily.get("temperature_2m_min", [])) else None,
            "precip_sum": daily["precipitation_sum"][i] if i < len(daily.get("precipitation_sum", [])) else None,
            "precip_prob_max": daily["precipitation_probability_max"][i] if i < len(daily.get("precipitation_probability_max", [])) else None,
            "weather_code": daily["weather_code"][i] if i < len(daily.get("weather_code", [])) else None,
            "wind_max": daily["wind_speed_10m_max"][i] if i < len(daily.get("wind_speed_10m_max", [])) else None,
            "hourly": hourly_by_date.get(date, []),
        })
    return days


def fetch_temp_letzigraben() -> dict:
    r = httpx.get(
        "https://www.badi-info.ch/_temp/zh/letzigraben.htm",
        timeout=15, follow_redirects=True, headers=HEADERS_WEB
    )
    r.raise_for_status()
    for pattern in [
        r"<strong[^>]*>\s*([\d.,]+)\s*</strong>",
        r"<b[^>]*>\s*([\d.,]+)\s*</b>",
    ]:
        m = re.search(pattern, r.text, re.IGNORECASE)
        if m:
            val = float(m.group(1).replace(",", "."))
            ts = re.search(r"(Mo|Di|Mi|Do|Fr|Sa|So)[^<]{0,5}[\d]{2}\.[\d]{2}[^<]{0,10}um\s*([\d:]+)", r.text)
            return {"temp": val, "measured_at": ts.group(0).strip() if ts else None, "source": "badi-info.ch"}
    return {"temp": None, "measured_at": None, "source": "badi-info.ch"}


def fetch_temp_utoquai() -> dict:
    try:
        r = httpx.get(
            "https://www.tecson-data.ch/zurich/tiefenbrunnen/index.php",
            timeout=15, follow_redirects=True, headers=HEADERS_WEB
        )
        if r.status_code == 200:
            m = re.search(r"(\d{1,2}[.,]\d)\s*°?\s*C", r.text)
            if m:
                val = float(m.group(1).replace(",", "."))
                return {"temp": val, "measured_at": "aktuell", "source": "tecson-data.ch"}
    except Exception as e:
        print(f"  tecson-data.ch nicht erreichbar: {e}")

    url = "https://data.stadt-zuerich.ch/dataset/sid_wapo_wetterstationen/download/messwerte_tiefenbrunnen_seit2007-heute.csv"
    r = httpx.get(url, timeout=30, follow_redirects=True,
                  headers={**HEADERS_WEB, "Range": "bytes=-5000"})
    text = r.text
    if r.status_code == 206:
        header_r = httpx.get(url, timeout=15, follow_redirects=True,
                             headers={**HEADERS_WEB, "Range": "bytes=0-300"})
        header_line = header_r.text.split("\n")[0]
        lines = text.split("\n")
        text = header_line + "\n" + "\n".join(lines[2:]) if len(lines) > 2 else text

    reader = csv.DictReader(io.StringIO(text))
    for row in reversed(list(reader)):
        wt = row.get("water_temperature", "").strip()
        if wt and wt not in ("", "NA", "NaN"):
            ts = row.get("timestamp_utc") or row.get("timestamp_cet")
            return {"temp": round(float(wt.replace(",", ".")), 1),
                    "measured_at": ts, "source": "OGD Stadt Zürich / Tiefenbrunnen"}
    return {"temp": None, "measured_at": None, "source": "OGD Stadt Zürich"}


def fetch_limmat_letten() -> dict:
    result = {"water_temp": None, "abfluss_m3s": None,
              "temp_measured_at": None, "flow_measured_at": None}

    try:
        r = httpx.get(
            "https://hydroproweb.zh.ch/Listen/AktuelleWerte/AktWassertemp.html",
            timeout=15, follow_redirects=True, headers=HEADERS_WEB
        )
        r.raise_for_status()
        update_m = re.search(r"Letztes Update:\s*(\d{2}\.\d{2}\.\d{4})", r.text)
        if update_m:
            from datetime import date
            update_date = update_m.group(1)
            today_str = date.today().strftime("%d.%m.%Y")
            if update_date != today_str:
                raise ValueError(f"Daten veraltet: {update_date}")
        idx = r.text.find("KW Letten")
        if idx < 0:
            raise ValueError("KW Letten nicht gefunden")
        snippet = r.text[idx:idx+400]
        nums = re.findall(r"\*\*(\d{1,2}[.,]\d)\*\*", snippet)
        if nums:
            result["water_temp"] = float(nums[0].replace(",", "."))
            time_m = re.search(r"\*\*(\d{2}:\d{2})\*\*", snippet)
            result["temp_measured_at"] = time_m.group(1) if time_m else None
        else:
            raise ValueError("Kein Wert")
    except Exception as e:
        print(f"  WARN hydroproweb: {e} – versuche existenz.ch")
        for station in ["2135", "2030", "2011"]:
            try:
                r2 = httpx.get(
                    f"https://api.existenz.ch/apiv1/hydro/latest"
                    f"?locations={station}&parameters=temperature&app=badi-tracker",
                    timeout=10, follow_redirects=True, headers=HEADERS_WEB
                )
                r2.raise_for_status()
                for entry in r2.json().get("payload", []):
                    if entry.get("par") == "temperature" and entry.get("val") is not None:
                        result["water_temp"] = round(float(entry["val"]), 1)
                        raw_ts = entry.get("timestamp") or entry.get("dt")
                        if isinstance(raw_ts, (int, float)):
                            result["temp_measured_at"] = datetime.fromtimestamp(raw_ts, tz=timezone.utc).isoformat()
                        else:
                            result["temp_measured_at"] = raw_ts
                        print(f"  Limmat Temp (existenz.ch Station {station}): {result['water_temp']}°C")
                        break
                if result["water_temp"] is not None:
                    break
            except Exception as e2:
                print(f"  WARN existenz.ch {station}: {e2}")

    try:
        r = httpx.get(
            "https://api.existenz.ch/apiv1/hydro/latest"
            "?locations=2099&parameters=flow&app=badi-tracker",
            timeout=15, follow_redirects=True, headers=HEADERS_WEB
        )
        r.raise_for_status()
        for entry in r.json().get("payload", []):
            if entry.get("par") == "flow" and entry.get("val") is not None:
                result["abfluss_m3s"] = round(float(entry["val"]), 1)
                raw_ts = entry.get("timestamp") or entry.get("dt")
                if isinstance(raw_ts, (int, float)):
                    result["flow_measured_at"] = datetime.fromtimestamp(raw_ts, tz=timezone.utc).isoformat()
                else:
                    result["flow_measured_at"] = raw_ts
                print(f"  Limmat Abfluss: {result['abfluss_m3s']} m³/s")
                break
    except Exception as e:
        print(f"  WARN Limmat-Abfluss: {e}")

    return result


def is_swiss_holiday(d: datetime) -> bool:
    try:
        import holidays
        return d.date() in holidays.country_holidays("CH", subdiv="ZH")
    except Exception:
        return False


def is_zh_school_holiday(d: datetime) -> bool:
    ferien = [
        ("2026-04-20", "2026-05-02"),
        ("2026-07-13", "2026-08-15"),
        ("2026-10-05", "2026-10-17"),
        ("2026-12-21", "2027-01-02"),
        ("2027-02-08", "2027-02-20"),
        ("2027-04-26", "2027-05-08"),
    ]
    today = d.date().isoformat()
    return any(s <= today <= e for s, e in ferien)


def main() -> None:
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN nicht gesetzt!")

    now_utc = datetime.now(timezone.utc)
    now_local = now_utc.astimezone(TZ)
    timestamp_iso = now_utc.isoformat(timespec="seconds")

    print(f"=== Badi-Tracker {timestamp_iso} ===")

    # 1. Auslastung
    snapshot = asyncio.run(fetch_snapshot())
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
            cf = int(entry.get("currentfill", 0) or 0)
            ms = int(entry.get("maxspace", 0) or 0)
            fs = int(entry.get("freespace", 0) or 0)
        except (ValueError, TypeError):
            cf = ms = fs = 0
        ratio = (cf / ms) if ms > 0 else 0.0
        rows.append({**context, "uid": entry.get("uid", ""), "name": entry.get("name", ""),
                     "currentfill": cf, "freespace": fs, "maxspace": ms,
                     "fill_ratio": round(ratio, 4)})

    fields = ["timestamp_utc", "timestamp_local", "weekday", "hour", "minute",
              "is_holiday", "is_school_holiday",
              "uid", "name", "currentfill", "freespace", "maxspace", "fill_ratio"]
    append_to_csv_in_repo("data/auslastung.csv", fields, rows)

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
        append_to_csv_in_repo("data/weather.csv", list(weather_row.keys()), [weather_row])
    except Exception as e:
        print(f"WARN Wetter: {e}")

    # 2b. Wettervorhersage alle 30 Min
    if now_local.minute in (0, 30):
        try:
            forecast = fetch_weather_forecast()
            existing, sha = gh_get_file("data/weather_forecast.json")
            gh_put_file(
                "data/weather_forecast.json",
                json.dumps({"updated_at": timestamp_iso, "days": forecast},
                           ensure_ascii=False, indent=2),
                sha,
                f"forecast: update {now_local.strftime('%H:%M')} CH-Zeit"
            )
            print(f"OK weather_forecast.json ({len(forecast)} Tage)")
        except Exception as e:
            print(f"WARN Forecast: {e}")

    # 3. Temperaturen alle 30 Min
    if now_local.minute in (0, 30):
        try:
            letzi = fetch_temp_letzigraben()
            print(f"OK Letzigraben: {letzi['temp']}°C ({letzi['measured_at']})")
        except Exception as e:
            print(f"WARN Letzi-Temp: {e}")
            letzi = {"temp": None, "measured_at": None, "source": "error"}
        try:
            uto = fetch_temp_utoquai()
            print(f"OK Utoquai: {uto['temp']}°C ({uto['measured_at']})")
        except Exception as e:
            print(f"WARN Uto-Temp: {e}")
            uto = {"temp": None, "measured_at": None, "source": "error"}

        letten = fetch_limmat_letten()
        print(f"OK Letten: {letten['water_temp']}°C, {letten['abfluss_m3s']} m³/s")

        temps = {
            "updated_at": timestamp_iso,
            "updated_at_local": now_local.strftime("%H:%M"),
            "city": {"temp": 28.0, "unit": "C", "source": "static",
                     "note": "Schwimmerbecken 28°C / Nichtschwimmer 32°C"},
            "letzigraben": {**letzi, "unit": "C"},
            "utoquai": {**uto, "unit": "C"},
            "letten": {**letten, "source": "hydroproweb.zh.ch / Limmat KW Letten"},
        }
        existing, sha = gh_get_file("data/temperatures.json")
        gh_put_file(
            "data/temperatures.json",
            json.dumps(temps, indent=2, ensure_ascii=False),
            sha,
            f"temps: update {now_local.strftime('%H:%M')} CH-Zeit"
        )
        print("OK temperatures.json committed")


if __name__ == "__main__":
    main()
