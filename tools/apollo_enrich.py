"""Enrich a LinkedIn-capture CSV via Apollo's people-match API — lookup only.

Reads the CSV exported by the LinkedIn capture extension (name, profile_url,
headline, ...) and adds email / title / company columns using Apollo person
matching. This is a pure data lookup: it spends enrichment credits but adds
NOBODY to Apollo lists, sequences, or any outreach campaign. Output feeds the
Voyce lead tracker and founder-sent LinkedIn DMs.

Usage:
    python tools/apollo_enrich.py captured.csv               # -> captured_enriched.csv
    python tools/apollo_enrich.py captured.csv -o leads.csv

Needs APOLLO_API_KEY in the environment or in backend/.env.
"""

import argparse
import csv
import os
import sys
import time

import requests

MATCH_URL = "https://api.apollo.io/api/v1/people/match"


def _load_key() -> str:
    key = os.getenv("APOLLO_API_KEY", "").strip()
    if not key:
        env_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                "backend", ".env")
        if os.path.exists(env_path):
            for line in open(env_path, encoding="utf-8"):
                if line.strip().startswith("APOLLO_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"')
                    break
    if not key:
        sys.exit("APOLLO_API_KEY not set (env var or backend/.env). Get it from Apollo → Settings → Integrations → API.")
    return key


def match(key: str, row: dict) -> dict:
    """One Apollo person-match call. Returns {} on any failure — never raises."""
    payload = {"linkedin_url": row.get("profile_url", "")}
    name = (row.get("name") or "").strip()
    if " " in name:
        payload["first_name"], payload["last_name"] = name.split(" ", 1)
    try:
        r = requests.post(MATCH_URL, json=payload,
                          headers={"X-Api-Key": key, "Content-Type": "application/json"},
                          timeout=20)
        if r.status_code == 429:
            time.sleep(15)  # rate limited — one patient retry
            r = requests.post(MATCH_URL, json=payload,
                              headers={"X-Api-Key": key, "Content-Type": "application/json"},
                              timeout=20)
        if not r.ok:
            return {}
        p = (r.json() or {}).get("person") or {}
        org = p.get("organization") or {}
        return {
            "email":   p.get("email") or "",
            "title":   p.get("title") or "",
            "company": org.get("name") or "",
            "city":    p.get("city") or "",
            "country": p.get("country") or "",
        }
    except requests.RequestException:
        return {}


def main() -> None:
    ap = argparse.ArgumentParser(description="Enrich a capture CSV via Apollo (lookup only, no lists).")
    ap.add_argument("csv_in", help="CSV exported by the LinkedIn capture extension")
    ap.add_argument("-o", "--out", help="output path (default: <input>_enriched.csv)")
    args = ap.parse_args()

    out_path = args.out or os.path.splitext(args.csv_in)[0] + "_enriched.csv"
    key = _load_key()

    with open(args.csv_in, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        sys.exit("Input CSV is empty.")

    extra = ["email", "title", "company", "city", "country"]
    fields = list(rows[0].keys()) + [c for c in extra if c not in rows[0]]

    hits = 0
    for i, row in enumerate(rows, 1):
        if row.get("email"):          # already enriched — don't spend a credit twice
            continue
        info = match(key, row)
        row.update({k: info.get(k, row.get(k, "")) for k in extra})
        if info.get("email"):
            hits += 1
        print(f"[{i}/{len(rows)}] {row.get('name', '?'):30s} -> {info.get('email') or 'no match'}")
        time.sleep(0.8)               # human-paced; stays well under Apollo rate limits

    with open(out_path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    print(f"\nDone: {hits}/{len(rows)} matched with email -> {out_path}")


if __name__ == "__main__":
    main()
