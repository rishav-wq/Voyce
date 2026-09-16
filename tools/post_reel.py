"""
Render a Voyce reel and publish it to Instagram — one command, no editor.

    python tools/post_reel.py --reel 11pm                # render + publish
    python tools/post_reel.py --reel 11pm --render-only  # write the MP4, publish nothing
    python tools/post_reel.py --list

Chain: make_reel_bed (music, synced to this reel's beats) -> reel_render (frames
-> ffmpeg, music muxed in the same pass) -> /media-upload (a public URL, because
Meta fetches media rather than accepting an upload) -> instagram.publish_reel.

Needs in backend/.env: IG_ACCESS_TOKEN, IG_USER_ID, MEDIA_UPLOAD_SECRET,
VOYCE_API_BASE. Publishing goes live immediately — use --render-only to preview.
"""

import argparse
import os
import sys
import time

import requests
from dotenv import dotenv_values

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import reel_render                      # noqa: E402
import reel_html                        # noqa: E402
import instagram                        # noqa: E402
from reel_demos import DEMOS, bed_timings  # noqa: E402
from make_reel_bed import REELS as BED_TIMINGS, build as build_bed  # noqa: E402

ENV = dotenv_values(os.path.join(ROOT, "backend", ".env"))
AUDIO_DIR = os.path.join(ROOT, "marketing", "audio")
OUT_DIR = os.path.join(ROOT, "marketing", "reels")


def ensure_bed(name: str, timings: dict = None) -> str:
    """The music bed is part of the render, not a manual afterthought."""
    path = os.path.join(AUDIO_DIR, f"voyce-bed-{name}.wav")
    if os.path.exists(path):
        return path
    tm = timings or BED_TIMINGS[name]
    print(f"  generating music bed for {name}...")
    build_bed(tm["dur"], tm["hook"], tm["turn"], tm["payoff"], path)
    return path


def upload(path: str) -> str:
    base = (ENV.get("VOYCE_API_BASE") or "").rstrip("/")
    secret = (ENV.get("MEDIA_UPLOAD_SECRET") or "").strip()
    if not base:
        sys.exit("VOYCE_API_BASE missing from backend/.env")
    if not secret:
        sys.exit("MEDIA_UPLOAD_SECRET missing from backend/.env")
    with open(path, "rb") as fh:
        res = requests.post(f"{base}/media-upload",
                            headers={"x-media-secret": secret},
                            files={"file": (os.path.basename(path), fh, "video/mp4")},
                            timeout=600)
    if res.status_code >= 400:
        sys.exit(f"upload failed {res.status_code}: {res.text[:300]}\n"
                 f"(is the deploy live and MEDIA_UPLOAD_SECRET set on the server?)")
    info = res.json()
    print(f"  hosted at {info['url']} ({info['bytes'] / 1024 / 1024:.1f} MB)")
    return info["url"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reel", help="which typographic reel to build")
    ap.add_argument("--demo", help="which product-demo reel to build (real UI, HTML scene)")
    ap.add_argument("--list", action="store_true", help="show available reels")
    ap.add_argument("--render-only", action="store_true", help="build the MP4, publish nothing")
    ap.add_argument("--caption", help="override the reel's stored caption")
    ap.add_argument("--crf", type=int, default=19, help="quality, lower is better (default 19)")
    a = ap.parse_args()

    if a.list or not (a.reel or a.demo):
        print("Typographic reels (--reel):")
        for k, s in reel_render.SPECS.items():
            print(f"  {k:<12} {s['dur']:.1f}s  \"{s['hook']}\"")
        print("\nProduct demos (--demo) — the real UI, rendered from the HTML scene:")
        for k, s in DEMOS.items():
            print(f"  {k:<12} {s['dur']:.1f}s  \"{s['hook']}\"")
        return

    is_demo = bool(a.demo)
    name = a.demo or a.reel

    if is_demo:
        if name not in DEMOS:
            sys.exit(f"unknown demo '{name}'. Try --list")
        spec = DEMOS[name]
        bed = ensure_bed(name, bed_timings(name))
        out = os.path.join(OUT_DIR, f"voyce-demo-{name}.mp4")
    else:
        if name not in reel_render.SPECS:
            sys.exit(f"unknown reel '{name}'. Try --list")
        spec = reel_render.SPECS[name]
        bed = ensure_bed(name)
        out = os.path.join(OUT_DIR, f"voyce-reel-{name}.mp4")

    t0 = time.time()
    total_frames = int(spec["dur"] * reel_render.FPS)
    kind = "demo (HTML scene)" if is_demo else "reel (PIL)"
    print(f"rendering {name} as {kind}: {spec['dur']:.1f}s, {total_frames} frames")

    def tick(i, total):
        pct = 100 * i / total
        sys.stdout.write(f"\r  frame {i}/{total} ({pct:.0f}%)")
        sys.stdout.flush()

    if is_demo:
        reel_html.render(spec, out, audio_path=bed, crf=a.crf, progress=tick)
    else:
        reel_render.render(name, out, audio_path=bed, crf=a.crf, progress=tick)
    size = os.path.getsize(out) / 1024 / 1024
    print(f"\r  wrote {out} ({size:.1f} MB) in {time.time() - t0:.0f}s")

    if a.render_only:
        print("render-only: nothing published. Open the file to check it.")
        return

    caption = a.caption if a.caption is not None else spec.get("caption", "")
    prof = instagram.me()
    print(f"publishing to @{prof.get('username')}")
    url = upload(out)
    media_id = instagram.publish_reel(url, caption)
    print(f"PUBLISHED - media id {media_id}")


if __name__ == "__main__":
    main()
