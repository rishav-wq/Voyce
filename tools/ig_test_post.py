"""
One-off Instagram publish test against the real @voyce.app account.

Usage (from repo root):
    python tools/ig_test_post.py --image-url https://.../something.jpg --caption "First API post"
    python tools/ig_test_post.py --check          # token + quota probe only, publishes nothing

The image URL must be public HTTPS JPEG. The post goes LIVE on the account —
delete it from the app afterwards if it was just a smoke test.
"""

import argparse
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import instagram  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image-url", help="public HTTPS JPEG to post")
    ap.add_argument("--caption", default="", help="post caption")
    ap.add_argument("--check", action="store_true", help="only verify token + quota, do not publish")
    args = ap.parse_args()

    profile = instagram.me()
    print(f"token OK → @{profile.get('username')} ({profile.get('account_type')})")
    quota = instagram.publish_limit()
    print(f"publish quota used (24h): {quota}")

    if args.check:
        return
    if not args.image_url:
        sys.exit("nothing to publish — pass --image-url or use --check")

    print("publishing…")
    media_id = instagram.publish_image(args.image_url, args.caption)
    print(f"PUBLISHED — media id {media_id}. Check the account; delete from the app if this was a smoke test.")


if __name__ == "__main__":
    main()
