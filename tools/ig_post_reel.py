"""
Publish a local video (e.g. a Claude Design export) to @voyce.app as a Reel.

Flow: upload the MP4 to the deployed backend's /media-upload (public URL) →
hand that URL to the Instagram API → poll ingestion → publish.

Usage (from repo root):
    python tools/ig_post_reel.py --file path\to\reel.mp4 --caption "..."
    python tools/ig_post_reel.py --file reel.mp4 --caption "..." --base https://voyce.onrender.com

Video requirements (Instagram Reels): MP4 (H.264 video, AAC audio), 9:16
(1080×1920 ideal), 3s–15min, under ~300MB. Ingestion can take a few minutes —
the script waits.

Needs in backend/.env: IG_ACCESS_TOKEN, IG_USER_ID, MEDIA_UPLOAD_SECRET,
and VOYCE_API_BASE (or pass --base) pointing at the deployed backend.
"""

import argparse
import os
import sys

import requests
from dotenv import dotenv_values

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
import instagram  # noqa: E402

_ENV = dotenv_values(os.path.join(os.path.dirname(__file__), "..", "backend", ".env"))


def upload(base: str, path: str, secret: str) -> str:
    with open(path, "rb") as f:
        res = requests.post(
            f"{base.rstrip('/')}/media-upload",
            headers={"x-media-secret": secret},
            files={"file": (os.path.basename(path), f, "video/mp4")},
            timeout=300,
        )
    if res.status_code >= 400:
        sys.exit(f"upload failed {res.status_code}: {res.text[:300]}")
    info = res.json()
    print(f"uploaded → {info['url']} ({info['bytes']} bytes)")
    return info["url"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", required=True, help="local .mp4 to publish")
    ap.add_argument("--caption", default="", help="reel caption")
    ap.add_argument("--base", default=_ENV.get("VOYCE_API_BASE", ""),
                    help="deployed backend base URL (or set VOYCE_API_BASE in backend/.env)")
    args = ap.parse_args()

    if not args.base:
        sys.exit("no backend URL — pass --base or set VOYCE_API_BASE in backend/.env")
    if not args.file.lower().endswith(".mp4"):
        sys.exit("Instagram Reels want an .mp4 (H.264/AAC)")
    secret = (_ENV.get("MEDIA_UPLOAD_SECRET") or "").strip()
    if not secret:
        sys.exit("MEDIA_UPLOAD_SECRET missing from backend/.env")

    profile = instagram.me()
    print(f"token OK → @{profile.get('username')}")

    video_url = upload(args.base, args.file, secret)
    print("publishing reel (ingestion can take a few minutes)…")
    media_id = instagram.publish_reel(video_url, args.caption)
    print(f"PUBLISHED — media id {media_id}")


if __name__ == "__main__":
    main()
