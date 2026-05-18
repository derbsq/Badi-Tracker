"""
Rekonstruiert auslastung.csv aus allen Git-Commits.
Einmalig ausführen – z.B. als Railway Manual Run oder lokal.
Benötigt: GITHUB_TOKEN Umgebungsvariable
"""
import base64
import csv
import io
import json
import os
import httpx

GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = "derbsq/Badi-Tracker"
GITHUB_API = "https://api.github.com"
HEADERS_GH = {
    "Authorization": f"Bearer {GITHUB_TOKEN}",
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}

def get_all_commits():
    """Holt alle Commits die data/auslastung.csv betreffen."""
    commits = []
    page = 1
    while True:
        r = httpx.get(
            f"{GITHUB_API}/repos/{GITHUB_REPO}/commits",
            headers=HEADERS_GH,
            params={"path": "data/auslastung.csv", "per_page": 100, "page": page},
            timeout=30
        )
        r.raise_for_status()
        batch = r.json() 
        if not batch:
            break
        commits.extend(batch)
        print(f"  Seite {page}: {len(batch)} Commits geladen ({len(commits)} total)")
        if len(batch) < 100:
            break
        page += 1
    return commits

def get_csv_at_commit(sha):
    """Holt den CSV-Inhalt eines bestimmten Commits."""
    r = httpx.get(
        f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/data/auslastung.csv",
        headers=HEADERS_GH,
        params={"ref": sha},
        timeout=15
    )
    if r.status_code == 404:
        return []
    r.raise_for_status()
    data = r.json()
    
    if not data.get("content") or data.get("content", "").strip() == "":
        # Grosse Datei: Blob API
        blob_sha = data.get("sha", "")
        r2 = httpx.get(
            f"{GITHUB_API}/repos/{GITHUB_REPO}/git/blobs/{blob_sha}",
            headers={**HEADERS_GH, "Accept": "application/vnd.github.raw+json"},
            timeout=60
        )
        r2.raise_for_status()
        text = r2.text
    else:
        text = base64.b64decode(data["content"]).decode("utf-8")
    
    if not text.strip():
        return []
    reader = csv.DictReader(io.StringIO(text))
    return list(reader)

def main():
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN nicht gesetzt!")
    
    print("Lade alle Commits...")
    commits = get_all_commits()
    print(f"Gesamt: {len(commits)} Commits gefunden")
    
    # Commits chronologisch sortieren (älteste zuerst)
    commits.reverse()
    
    # Alle einzigartigen Zeilen sammeln (dedupliziert per timestamp_utc + uid)
    all_rows = {}
    fields = None
    
    for i, commit in enumerate(commits):
        sha = commit["sha"]
        date = commit["commit"]["committer"]["date"]
        
        try:
            rows = get_csv_at_commit(sha)
            if rows and not fields:
                fields = list(rows[0].keys())
            
            new_count = 0
            for row in rows:
                key = row.get("timestamp_utc", "") + "|" + row.get("uid", "")
                if key not in all_rows:
                    all_rows[key] = row
                    new_count += 1
            
            if i % 20 == 0 or new_count > 0:
                print(f"  [{i+1}/{len(commits)}] {date[:10]} {sha[:8]}: +{new_count} neue Zeilen ({len(all_rows)} total)")
        except Exception as e:
            print(f"  FEHLER bei {sha[:8]}: {e}")
    
    if not all_rows:
        print("Keine Daten gefunden!")
        return
    
    # Sortiert nach timestamp_utc ausgeben
    sorted_rows = sorted(all_rows.values(), key=lambda r: r.get("timestamp_utc", ""))
    
    # CSV schreiben
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(sorted_rows)
    
    # Ins Repo commiten
    import base64 as b64
    content = buf.getvalue()
    print(f"\nRekonstruiert: {len(sorted_rows)} Zeilen")
    print(f"Zeitraum: {sorted_rows[0]['timestamp_utc']} bis {sorted_rows[-1]['timestamp_utc']}")
    
    # Aktuelle SHA holen
    r = httpx.get(
        f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/data/auslastung.csv",
        headers=HEADERS_GH, params={"ref": "main"}, timeout=15
    )
    current_sha = r.json().get("sha", "")
    
    # Hochladen
    payload = {
        "message": f"restore: CSV rekonstruiert aus Git-History ({len(sorted_rows)} Zeilen)",
        "content": b64.b64encode(content.encode("utf-8")).decode("ascii"),
        "branch": "main",
        "sha": current_sha,
    }
    r2 = httpx.put(
        f"{GITHUB_API}/repos/{GITHUB_REPO}/contents/data/auslastung.csv",
        headers=HEADERS_GH, json=payload, timeout=60
    )
    r2.raise_for_status()
    print("✅ CSV erfolgreich ins Repo hochgeladen!")

if __name__ == "__main__":
    main()
