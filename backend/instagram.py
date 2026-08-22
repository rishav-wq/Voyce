"""
instagram.py — Instagram content publishing via the official Instagram API
(Instagram Login variant, graph.instagram.com).

v1 scope: publish to the founder's own account using the long-lived token in
backend/.env (IG_ACCESS_TOKEN / IG_USER_ID). The db-backed multi-user token
store can mirror li_tokens later, when IG ships as a Voyce feature.

Publish flow (Meta requirement): every image/video must be reachable at a
public HTTPS URL — the API pulls media from a URL, it does not accept uploads.
Images must be JPEG. Carousels: 2–10 children. Rate limit: 100 API-published
posts per account per rolling 24h.
"""

import logging
import os
import time
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

# Explicitly load backend/.env — a bare load_dotenv() resolves from CWD and can
# pick up the root .env, which doesn't carry the IG_* keys (same pattern as main.py).
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

GRAPH = "https://graph.instagram.com/v21.0"

IG_APP_ID = os.getenv("IG_APP_ID", "")
IG_APP_SECRET = os.getenv("IG_APP_SECRET", "")
IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN", "")
IG_USER_ID = os.getenv("IG_USER_ID", "")

# Container status polling — images are usually instant, videos/carousels can
# take a while to process before they may be published.
_POLL_INTERVAL_S = 3
_POLL_TIMEOUT_S = 120


# Refresh this far ahead of expiry — a weekly cron gets several attempts before
# the token actually dies, so one failed week is survivable.
_REFRESH_WINDOW_DAYS = 15
_ACCOUNT_KEY = "founder"   # single-account today; keyed so multi-user can slot in


class InstagramError(RuntimeError):
    pass


def _check(res: requests.Response) -> dict:
    if res.status_code >= 400:
        raise InstagramError(f"Instagram API {res.status_code}: {res.text[:500]}")
    return res.json()


# ── Token store ───────────────────────────────────────────────────────────────
# Long-lived tokens expire in ~60 days, so the value in .env / Render's dashboard
# goes stale and cannot be rewritten by the running app. Mongo is the writable
# home; env is the seed and the fallback when Mongo is unreachable (CLI tools).

def _token_doc() -> dict:
    try:
        import db
        return db.ig_tokens.find_one({"account": _ACCOUNT_KEY}, {"_id": 0}) or {}
    except Exception:
        return {}


def get_token() -> str:
    return (_token_doc().get("access_token") or IG_ACCESS_TOKEN or "").strip()


def get_user_id() -> str:
    return (_token_doc().get("user_id") or IG_USER_ID or "").strip()


def save_token(token: str, expires_in: int = 0, user_id: str = ""):
    """Persist a token so the next refresh has something current to work from."""
    entry = {
        "account": _ACCOUNT_KEY,
        "access_token": token,
        "obtained_at": datetime.utcnow(),
        "user_id": user_id or get_user_id(),
    }
    if expires_in:
        entry["expires_at"] = datetime.utcnow() + timedelta(seconds=int(expires_in))
    try:
        import db
        db.ig_tokens.replace_one({"account": _ACCOUNT_KEY}, entry, upsert=True)
    except Exception:
        logging.exception("Could not persist the Instagram token — it stays in env only")


def me(token: str = "") -> dict:
    """Profile of the token's account — the cheapest 'is the token alive' probe."""
    res = requests.get(
        f"{GRAPH}/me",
        params={"fields": "user_id,username,account_type", "access_token": token or get_token()},
        timeout=15,
    )
    return _check(res)


def refresh_token(token: str = "") -> dict:
    """Extend a long-lived token by ~60 days and persist the replacement.
    Meta requires the token be at least 24h old and not yet expired."""
    res = requests.get(
        "https://graph.instagram.com/refresh_access_token",
        params={"grant_type": "ig_refresh_token", "access_token": token or get_token()},
        timeout=15,
    )
    data = _check(res)   # {"access_token": ..., "token_type": ..., "expires_in": seconds}
    if data.get("access_token"):
        save_token(data["access_token"], data.get("expires_in", 0))
    return data


def token_status() -> dict:
    """What the store knows about the current token — for the refresh job and
    for answering 'is this about to die?' without spending an API call."""
    doc = _token_doc()
    expires_at = doc.get("expires_at")
    days_left = None
    if expires_at:
        days_left = round((expires_at - datetime.utcnow()).total_seconds() / 86400, 1)
    return {
        "source": "db" if doc.get("access_token") else ("env" if IG_ACCESS_TOKEN else "missing"),
        "obtained_at": doc.get("obtained_at"),
        "expires_at": expires_at,
        "days_left": days_left,
    }


def refresh_if_stale() -> dict:
    """Weekly-cron entry point. Refreshes when expiry is inside the window, or
    when the store has never recorded an expiry (the env-seeded first run).
    Returns the new status, or {} when no action was needed."""
    if not get_token():
        logging.warning("Instagram token refresh skipped — no token configured")
        return {}
    status = token_status()
    days_left = status["days_left"]
    if days_left is not None and days_left > _REFRESH_WINDOW_DAYS:
        return {}
    refresh_token()
    new_status = token_status()
    logging.info("Instagram token refreshed — %s days left", new_status.get("days_left"))
    return new_status


def _create_container(params: dict, token: str, ig_user_id: str) -> str:
    res = requests.post(
        f"{GRAPH}/{ig_user_id}/media",
        data={**params, "access_token": token},
        timeout=30,
    )
    return _check(res)["id"]


def _wait_until_ready(container_id: str, token: str, timeout_s: int = _POLL_TIMEOUT_S):
    """Poll until Meta finishes ingesting the media (FINISHED), or fail loudly."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        res = requests.get(
            f"{GRAPH}/{container_id}",
            params={"fields": "status_code", "access_token": token},
            timeout=15,
        )
        status = _check(res).get("status_code", "")
        if status == "FINISHED":
            return
        if status == "ERROR":
            raise InstagramError(f"container {container_id} failed ingestion (status ERROR)")
        time.sleep(_POLL_INTERVAL_S)
    raise InstagramError(f"container {container_id} not ready after {timeout_s}s")


def _publish_container(container_id: str, token: str, ig_user_id: str) -> str:
    res = requests.post(
        f"{GRAPH}/{ig_user_id}/media_publish",
        data={"creation_id": container_id, "access_token": token},
        timeout=30,
    )
    return _check(res)["id"]


def publish_image(image_url: str, caption: str = "", token: str = "", ig_user_id: str = "") -> str:
    """Single-image feed post. image_url must be a public HTTPS JPEG.
    Returns the published media id."""
    token = token or get_token()
    ig_user_id = ig_user_id or get_user_id()
    container = _create_container({"image_url": image_url, "caption": caption}, token, ig_user_id)
    _wait_until_ready(container, token)
    return _publish_container(container, token, ig_user_id)


def publish_carousel(image_urls: list, caption: str = "", token: str = "", ig_user_id: str = "") -> str:
    """Carousel feed post from 2–10 public HTTPS JPEG URLs. Returns the media id."""
    if not 2 <= len(image_urls) <= 10:
        raise InstagramError(f"carousel needs 2–10 images, got {len(image_urls)}")
    token = token or get_token()
    ig_user_id = ig_user_id or get_user_id()

    children = []
    for url in image_urls:
        child = _create_container({"image_url": url, "is_carousel_item": "true"}, token, ig_user_id)
        _wait_until_ready(child, token)
        children.append(child)

    parent = _create_container(
        {"media_type": "CAROUSEL", "children": ",".join(children), "caption": caption},
        token, ig_user_id,
    )
    _wait_until_ready(parent, token)
    return _publish_container(parent, token, ig_user_id)


def publish_reel(video_url: str, caption: str = "", token: str = "", ig_user_id: str = "") -> str:
    """Reel from a public HTTPS MP4 URL. Ingestion is slow — polling does the waiting."""
    token = token or get_token()
    ig_user_id = ig_user_id or get_user_id()
    container = _create_container(
        {"media_type": "REELS", "video_url": video_url, "caption": caption},
        token, ig_user_id,
    )
    _wait_until_ready(container, token, timeout_s=600)  # video ingestion is slow
    return _publish_container(container, token, ig_user_id)


def publish_limit(token: str = "", ig_user_id: str = "") -> dict:
    """How much of the 100-posts/24h API quota is used."""
    token = token or get_token()
    ig_user_id = ig_user_id or get_user_id()
    res = requests.get(
        f"{GRAPH}/{ig_user_id}/content_publishing_limit",
        params={"access_token": token},
        timeout=15,
    )
    return _check(res)
