"""Merge, dedupe and triage LinkedIn capture CSVs into an outreach list.

Capture files overlap heavily (rescans of the same post) and comment sections in
this niche are full of people who SELL the same thing Voyce does. This sorts one
folder of captures into three buckets so Apollo credits only get spent on buyers:

    <out>_icp.csv          -> consultants / fractionals / coaches / solo founders
    <out>_competitor.csv   -> ghostwriters, personal-branding & LinkedIn-growth sellers
    <out>_other.csv        -> everyone else (students, engineers, corporate roles)

Usage:
    python tools/triage_captures.py "C:/Users/ASUS/Downloads"
    python tools/triage_captures.py "C:/Users/ASUS/Downloads" -o leads
"""

import argparse
import csv
import glob
import os
import re

# Sells content/branding services = competitor, never a buyer. Checked first.
COMPETITOR = [
    "ghostwriter", "ghostwriting", "personal brand", "personalbranding", "personal-branding",
    "linkedin growth", "linkedin strategist", "linkedin coach", "linkedin consultant",
    "linkedin management", "linkedin content", "content strategist", "content writer",
    "copywriter", "copywriting", "social media manager", "social media marketing",
    "smm", "authority engine", "authority system", "done-for-you", "dfy",
    "profile optimization", "image consultant", "image specialist", "brand strategist",
    "influencer marketing", "ugc", "seo", "paid ads", "paid media", "performance marketer",
    "meta ads", "google ads", "growth strategist", "lead generation", "lead gen",
    "i help founders build", "i help coaches", "i write", "turn your expertise",
]

# The Voyce beachhead: people who sell expertise and need to stay visible.
ICP = [
    "fractional cmo", "fractional cfo", "fractional coo", "fractional cro", "fractional cto",
    "fractional executive", "fractional marketing", "fractional",
    "consultant", "consulting", "advisor", "advisory", "coach", "principal",
    "founder", "ceo", "managing director", "solopreneur", "independent",
]

# Never a buyer regardless of other matches.
HARD_OUT = [
    "student", "aspiring", "intern", "fresher", "learning", "b.tech", "data analyst",
    "data engineer", "software engineer", "developer", "devops", "full stack",
    "recruiter", "talent", "hiring @", "professor", "academician",
]


# Wins over everything: a fractional exec IS the beachhead, even when their headline
# also says "personal branding" (they brand themselves, they don't sell branding).
HARD_ICP = ["fractional cmo", "fractional cfo", "fractional coo", "fractional cro",
            "fractional cto", "fractional chief", "fractional executive", "fractional marketing"]


def bucket(headline: str) -> str:
    h = (headline or "").lower()
    if not h.strip():
        return "other"                       # no signal, don't spend a credit
    if any(k in h for k in HARD_ICP):
        return "icp"
    if any(k in h for k in HARD_OUT):
        return "other"
    if any(k in h for k in COMPETITOR):
        return "competitor"
    if any(k in h for k in ICP):
        return "icp"
    return "other"


def norm(url: str) -> str:
    return re.sub(r"[?#].*$", "", (url or "").strip().lower()).rstrip("/")


def main() -> None:
    ap = argparse.ArgumentParser(description="Merge + dedupe + triage LinkedIn capture CSVs.")
    ap.add_argument("folder", help="folder containing linkedin-capture-*.csv")
    ap.add_argument("-o", "--out", default="voyce-leads", help="output prefix (default: voyce-leads)")
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.folder, "linkedin-capture*.csv")))
    if not files:
        raise SystemExit(f"No linkedin-capture*.csv found in {args.folder}")

    people, rows_read = {}, 0
    for path in files:
        with open(path, newline="", encoding="utf-8-sig") as f:
            for row in csv.DictReader(f):
                rows_read += 1
                key = norm(row.get("profile_url", ""))
                if not key:
                    continue
                seen = people.get(key)
                if seen:
                    # keep the richer headline; remember every post they showed up on
                    if len(row.get("headline", "")) > len(seen.get("headline", "")):
                        seen["headline"] = row["headline"]
                    posts = set(filter(None, seen["captured_from"].split("|")))
                    posts.add(row.get("captured_from", ""))
                    seen["captured_from"] = "|".join(sorted(filter(None, posts)))
                else:
                    people[key] = {
                        "name": row.get("name", "").strip(),
                        "profile_url": row.get("profile_url", "").strip(),
                        "headline": row.get("headline", "").strip(),
                        "captured_from": row.get("captured_from", "").strip(),
                    }

    buckets = {"icp": [], "competitor": [], "other": []}
    for p in people.values():
        p["bucket"] = bucket(p["headline"])
        p["email"] = ""          # filled later by apollo_enrich.py
        p["title"] = ""
        p["company"] = ""
        p["status"] = "new"      # your tracker column: new / dm_sent / replied / trial / paid
        buckets[p["bucket"]].append(p)

    fields = ["name", "profile_url", "headline", "title", "company", "email",
              "captured_from", "bucket", "status"]
    for name, rows in buckets.items():
        out = f"{args.out}_{name}.csv"
        rows.sort(key=lambda r: r["name"].lower())
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(rows)
        print(f"{name:11s} {len(rows):4d} -> {out}")

    print(f"\n{rows_read} rows across {len(files)} files -> {len(people)} unique people")
    print(f"Enrich ONLY the ICP file: python tools/apollo_enrich.py {args.out}_icp.csv")


if __name__ == "__main__":
    main()
