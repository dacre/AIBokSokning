#!/usr/bin/env python3
"""
run_spanaren.py – vecklig orkestrator för biblioteksspanaren.

Hämtar Goodreads to-read-listan, kontrollerar en batch mot
Stockholms stadsbiblioteks katalog och uppdaterar cursor.json
samt bibliotek.json. Körs utan argument – all konfiguration
läses från cursor.json och de hårdkodade konstanterna nedan.

Typisk körning (manuell):
  python3 scripts/run_spanaren.py

Via GitHub Actions (schemalagd):
  Se .github/workflows/biblioteksspanaren.yml
"""

import json
import subprocess
import sys
import time
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CURSOR_FILE = REPO_ROOT / "cursor.json"
BIBLIOTEK_FILE = REPO_ROOT / "bibliotek.json"
CHECKER_SCRIPT = Path(__file__).resolve().parent / "biblioteksspanaren.py"

GOODREADS_RSS_URL = (
    "https://www.goodreads.com/review/list_rss/910448?shelf=to-read&page={page}"
)
BATCH_SIZE = 40


def log(msg):
    print(msg, file=sys.stderr, flush=True)


def strip_ns(tag):
    return tag.split("}", 1)[1] if "}" in tag else tag


def fetch_page(page):
    url = GOODREADS_RSS_URL.format(page=page)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def parse_rss(xml_bytes):
    root = ET.fromstring(xml_bytes)
    books = []
    for node in root.iter():
        if strip_ns(node.tag) != "item":
            continue
        fields = {strip_ns(c.tag): (c.text or "").strip() for c in node}
        title = fields.get("title", "")
        if not title:
            continue
        isbn = fields.get("isbn13", "") or fields.get("isbn", "")
        if isbn in ("0", "0000000000000", "N/A"):
            isbn = ""
        books.append({
            "titel": title,
            "forfattare": fields.get("author_name", ""),
            "isbn": isbn,
        })
    return books


def fetch_all_books():
    all_books = []
    for page in range(1, 200):
        log(f"Hämtar RSS-sida {page}...")
        try:
            data = fetch_page(page)
        except Exception as exc:
            log(f"Fel vid sida {page}: {exc}")
            break
        books = parse_rss(data)
        if not books:
            log("Tom sida – listan slut.")
            break
        all_books.extend(books)
        time.sleep(1.5)
    return all_books


def read_json(path, default):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")


def main():
    cursor = read_json(CURSOR_FILE, {"bibliotek_index": 0})
    index = int(cursor.get("bibliotek_index", 0))
    log(f"Startar från index {index} (batch-storlek {BATCH_SIZE})")

    all_books = fetch_all_books()
    total = len(all_books)
    log(f"Totalt {total} böcker i to-read-listan")

    if not all_books:
        log("Inga böcker hämtades – avbryter.")
        sys.exit(1)

    if index >= total:
        log(f"Index {index} >= {total} – återställer till 0")
        index = 0

    batch = all_books[index: index + BATCH_SIZE]
    log(f"Kontrollerar #{index + 1}–#{index + len(batch)} av {total}")

    result = subprocess.run(
        [sys.executable, str(CHECKER_SCRIPT)],
        input=json.dumps(batch, ensure_ascii=False),
        capture_output=True,
        text=True,
    )
    log(result.stderr)
    if result.returncode != 0:
        log(f"biblioteksspanaren.py misslyckades (exit {result.returncode})")
        sys.exit(1)

    output = json.loads(result.stdout)
    nya = output.get("tillgangliga", [])

    # Vid index 0 startar vi ny cykel – töm gammal lista
    befintliga = [] if index == 0 else read_json(BIBLIOTEK_FILE, {}).get("tillgangliga", [])
    if index == 0:
        log("Ny cykel – startar om med tom lista i bibliotek.json")

    bibliotek = {
        "uppdaterad": date.today().isoformat(),
        "tillgangliga": befintliga + nya,
    }
    write_json(BIBLIOTEK_FILE, bibliotek)
    log(f"bibliotek.json: {len(bibliotek['tillgangliga'])} böcker totalt inne")

    next_index = index + BATCH_SIZE
    if next_index >= total:
        next_index = 0
        log("Listan slut – index återställt till 0")

    write_json(CURSOR_FILE, {
        "bibliotek_index": next_index,
        "senast_kord": date.today().isoformat(),
    })
    log(f"cursor.json: nästa index = {next_index}")

    summary = {
        "batch": f"#{index + 1}–#{index + len(batch)} av {total}",
        "ny_tillgangliga": len(nya),
        "totalt_inne": len(bibliotek["tillgangliga"]),
        "titlar": [b["titel"] for b in nya],
        "nasta_index": next_index,
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
