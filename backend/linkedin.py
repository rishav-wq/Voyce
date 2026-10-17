import os
import base64
import hmac
import json
from datetime import datetime, timedelta, timezone

import requests
from dotenv import load_dotenv

import db

load_dotenv()

CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8000/auth/linkedin/callback")
SCOPES = "openid profile w_member_social"

# LinkedIn retires each monthly version of its /rest APIs about a year after
# release, and a retired one can start failing at any time. 202503 sat past its
# 2026-03-16 sunset for seven months. 202609 is active until 2027-09-15; see
# learn.microsoft.com/linkedin/marketing/integrations/migrations. Override with
# LINKEDIN_API_VERSION to move without a code change.
API_VERSION = os.getenv("LINKEDIN_API_VERSION", "202609")


def _warn_if_version_ageing():
    try:
        y, m = int(API_VERSION[:4]), int(API_VERSION[4:6])
        sunset = datetime(y + 1, m, 15, tzinfo=timezone.utc)
        days = (sunset - datetime.now(timezone.utc)).days
        if days < 60:
            import logging
            logging.warning(f"LinkedIn API version {API_VERSION} sunsets in ~{days} days — "
                            "bump LINKEDIN_API_VERSION (see the migrations page).")
    except Exception:
        pass


_warn_if_version_ageing()



def _get_token_entry(user_id: str) -> dict:
    doc = db.li_tokens.find_one({"user_id": user_id}, {"_id": 0})
    return doc or {}


def _save_token_entry(user_id: str, entry: dict):
    db.li_tokens.replace_one({"user_id": user_id}, {"user_id": user_id, **entry}, upsert=True)


def get_auth_url(state: str) -> str:
    return (
        "https://www.linkedin.com/oauth/v2/authorization"
        f"?response_type=code"
        f"&client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}"
        f"&scope={SCOPES.replace(' ', '%20')}"
        f"&state={state}"
    )


OAUTH_STATE_TTL = timedelta(minutes=10)


# ── Connections ──────────────────────────────────────────────────────────────
# A "connection" is one LinkedIn member's token. The account owner's own
# LinkedIn is keyed by their user id (as it always was). A profile can instead
# post to a different member — say, a fractional CMO's client — keyed
# "profile:<id>" and recorded on the profile as `li_conn`. Every helper below
# takes a connection key, so the publishing code never cares which kind it is.

def profile_conn_key(company_id: str) -> str:
    return f"profile:{company_id}"


def connection_for(company: dict | None, user_id: str = "") -> str:
    """The LinkedIn connection a profile publishes through."""
    if company and company.get("li_conn"):
        return company["li_conn"]
    return (company or {}).get("user_id") or user_id


def connection_info(conn: str) -> dict:
    entry = _get_token_entry(conn)
    exp = token_expires_at(conn)
    return {"connected": bool(entry.get("access_token")) and not token_expired(conn),
            "name": entry.get("name", ""),
            "expires_at": exp.isoformat() if exp else None}


def register_state(state: str, target, nonce: str):
    """Remember a connect attempt. `target` says where the token goes:
    {"conn", "owner_id", optional "company_id" and "invite_id"} (a bare user id
    means the owner's own LinkedIn). `nonce` also goes to the starting browser
    as an HttpOnly cookie; the callback must present both. Stored in Mongo, not
    memory, so a deploy in the middle of connecting doesn't break it."""
    if isinstance(target, str):
        target = {"conn": target, "owner_id": target}
    db.oauth_states.insert_one({"_id": state, "user_id": target.get("owner_id", ""), "target": target,
                                "nonce": nonce, "created_at": datetime.now(timezone.utc)})


def consume_state(state: str, nonce: str) -> dict | None:
    """Where to attach the LinkedIn account — only for a state that is unused,
    under 10 minutes old, and returning to the browser that started it.

    Without the browser check, anyone could start a connect, send the genuine
    LinkedIn link to someone else, and have that person's LinkedIn attached to
    their own Voyce account the moment they clicked Allow."""
    if not state:
        return None
    doc = db.oauth_states.find_one_and_delete({"_id": state})   # one use, even if it fails below
    if not doc or not nonce or not hmac.compare_digest(str(doc.get("nonce", "")), nonce):
        return None
    created = doc.get("created_at")
    if isinstance(created, datetime):
        if created.tzinfo is None:
            created = created.replace(tzinfo=timezone.utc)   # Mongo hands dates back naive (UTC)
        if datetime.now(timezone.utc) - created > OAUTH_STATE_TTL:
            return None
    target = doc.get("target") or {"conn": doc.get("user_id", ""), "owner_id": doc.get("user_id", "")}
    return target if target.get("conn") else None


def exchange_code_for_token(code: str) -> dict:
    response = requests.post(
        "https://www.linkedin.com/oauth/v2/accessToken",
        data={
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": REDIRECT_URI,
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    response.raise_for_status()
    return response.json()


def _decode_id_token(id_token: str) -> dict:
    payload = id_token.split('.')[1]
    payload += '=' * (4 - len(payload) % 4)
    return json.loads(base64.b64decode(payload))


def save_token(user_id: str, token_data: dict):
    entry = {"access_token": token_data["access_token"]}
    # LinkedIn access tokens last 60 days and the exchange tells us exactly how
    # many seconds are left. That was being discarded, so is_connected() could
    # only ever answer "a token string exists" — the sidebar kept reading
    # "LinkedIn Connected" long after the token died, and the first sign of
    # trouble was a post failing.
    expires_in = int(token_data.get("expires_in") or 0)
    if expires_in > 0:
        entry["expires_at"] = (datetime.now(timezone.utc)
                               + timedelta(seconds=expires_in)).isoformat()
    if "id_token" in token_data:
        claims = _decode_id_token(token_data["id_token"])
        entry["person_id"] = claims.get("sub", "")
        # Shown on the profile card ("Posts as: Jane Smith"), so it's clear
        # which LinkedIn account each profile publishes to.
        entry["name"] = (claims.get("name")
                         or " ".join(x for x in (claims.get("given_name"), claims.get("family_name")) if x)
                         or "")
    _save_token_entry(user_id, entry)


def get_token(user_id: str) -> str | None:
    return _get_token_entry(user_id).get("access_token")


def token_expires_at(user_id: str) -> datetime | None:
    raw = _get_token_entry(user_id).get("expires_at")
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def token_expired(user_id: str) -> bool:
    """True only when we know it has expired.

    Tokens saved before expiry was recorded have no expires_at. Those are left
    alone rather than signing everyone out the moment this deploys — they will
    get a real expiry the next time they reconnect.
    """
    at = token_expires_at(user_id)
    return at is not None and at <= datetime.now(timezone.utc)


def is_connected(user_id: str) -> bool:
    return bool(_get_token_entry(user_id).get("access_token")) and not token_expired(user_id)


def logout(user_id: str):
    db.li_tokens.delete_one({"user_id": user_id})


def _get_person_id(user_id: str) -> str:
    person_id = _get_token_entry(user_id).get("person_id", "")
    if not person_id:
        raise ValueError("Person ID not found. Please reconnect LinkedIn.")
    return person_id


def describe_api_failure(status: int, body: str = "") -> str:
    """Turn a LinkedIn API status into something the user can act on.

    Every failure used to surface as "Failed to post to LinkedIn. Please
    reconnect LinkedIn and try again." — which is wrong advice for a rate limit
    or a duplicate post, and confusing next to a sidebar reading "Connected".
    """
    snippet = (body or "").strip()[:180]
    if status in (401, 403):
        return ("LinkedIn rejected the request — your access has expired or was revoked. "
                "Reconnect LinkedIn from the sidebar and post again.")
    if status == 422:
        return ("LinkedIn refused this post. It is usually a duplicate of something you "
                "already published, or the text breaks one of their formatting rules."
                + (f" LinkedIn said: {snippet}" if snippet else ""))
    if status == 429:
        return ("LinkedIn is rate-limiting this account right now. Wait a few minutes "
                "and try again — the post text is still here.")
    if 500 <= status < 600:
        return (f"LinkedIn is having problems on their side ({status}). Try again in a "
                f"few minutes — nothing was published.")
    return (f"LinkedIn rejected the post ({status})."
            + (f" They said: {snippet}" if snippet else " Try again shortly."))


def _escape_little_text(text: str) -> str:
    """The /rest/posts Posts API parses `commentary` as LinkedIn 'little text', where
    ( ) [ ] { } @ < > * _ ~ | \\ are reserved formatting tokens — an unescaped '('
    silently truncated a live post at its first list item. Backslash-escape every
    literal use. '#' is deliberately left alone so hashtags keep linking.
    (The older ugcPosts path takes plain text and must NOT be escaped.)"""
    out = []
    for ch in text or "":
        if ch in "\\|{}@[]()<>*_~":
            out.append("\\" + ch)
        else:
            out.append(ch)
    return "".join(out)


def upload_and_post_carousel(user_id: str, pdf_bytes: bytes, post_text: str, title: str = "Carousel") -> dict:
    access_token = get_token(user_id)
    if not access_token:
        raise ValueError("LinkedIn not connected")
    person_id = _get_person_id(user_id)
    headers_base = {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": API_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }

    # Step 1: Initialize upload
    init_res = requests.post(
        "https://api.linkedin.com/rest/documents?action=initializeUpload",
        json={"initializeUploadRequest": {"owner": f"urn:li:person:{person_id}"}},
        headers={**headers_base, "Content-Type": "application/json"},
        timeout=15,
    )
    if not init_res.ok:
        raise ValueError("Carousel upload: " + describe_api_failure(init_res.status_code, init_res.text))
    init_res.raise_for_status()
    val = init_res.json()["value"]
    upload_url   = val["uploadUrl"]
    document_urn = val["document"]

    # Step 2: Upload PDF binary
    requests.put(upload_url, data=pdf_bytes,
                 headers={"Content-Type": "application/octet-stream"},
                 timeout=30).raise_for_status()

    # Step 3: Create document post
    payload = {
        "author": f"urn:li:person:{person_id}",
        "commentary": _escape_little_text(post_text),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {"media": {"id": document_urn, "title": title}},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    post_res = requests.post(
        "https://api.linkedin.com/rest/posts",
        json=payload,
        headers={**headers_base, "Content-Type": "application/json"},
        timeout=15,
    )
    if not post_res.ok:
        raise ValueError("Carousel post: " + describe_api_failure(post_res.status_code, post_res.text))
    post_res.raise_for_status()
    return {"status": "posted", "type": "carousel", "id": post_res.headers.get("x-restli-id", "")}


def upload_and_post_image(user_id: str, image_bytes: bytes, post_text: str, alt_text: str = "") -> dict:
    access_token = get_token(user_id)
    if not access_token:
        raise ValueError("LinkedIn not connected")
    person_id = _get_person_id(user_id)
    headers_base = {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": API_VERSION,
        "X-Restli-Protocol-Version": "2.0.0",
    }

    # Step 1: Initialize image upload
    init_res = requests.post(
        "https://api.linkedin.com/rest/images?action=initializeUpload",
        json={"initializeUploadRequest": {"owner": f"urn:li:person:{person_id}"}},
        headers={**headers_base, "Content-Type": "application/json"},
        timeout=15,
    )
    if not init_res.ok:
        raise ValueError("Image upload: " + describe_api_failure(init_res.status_code, init_res.text))
    val = init_res.json()["value"]
    upload_url = val["uploadUrl"]
    image_urn  = val["image"]

    # Step 2: Upload image binary
    requests.put(upload_url, data=image_bytes,
                 headers={"Content-Type": "application/octet-stream"},
                 timeout=30).raise_for_status()

    # Step 3: Create image post
    payload = {
        "author": f"urn:li:person:{person_id}",
        "commentary": _escape_little_text(post_text),
        "visibility": "PUBLIC",
        "distribution": {
            "feedDistribution": "MAIN_FEED",
            "targetEntities": [],
            "thirdPartyDistributionChannels": [],
        },
        "content": {"media": {"id": image_urn, "altText": (alt_text or "Branded image post")[:300]}},
        "lifecycleState": "PUBLISHED",
        "isReshareDisabledByAuthor": False,
    }
    post_res = requests.post(
        "https://api.linkedin.com/rest/posts",
        json=payload,
        headers={**headers_base, "Content-Type": "application/json"},
        timeout=15,
    )
    if not post_res.ok:
        raise ValueError("Image post: " + describe_api_failure(post_res.status_code, post_res.text))
    return {"status": "posted", "type": "image", "id": post_res.headers.get("x-restli-id", "")}


def get_post_engagement(user_id: str, post_urn: str) -> dict:
    access_token = get_token(user_id)
    if not access_token or not post_urn:
        return {}
    from urllib.parse import quote
    encoded = quote(post_urn, safe="")
    r = requests.get(
        f"https://api.linkedin.com/rest/socialActions/{encoded}",
        headers={
            "Authorization": f"Bearer {access_token}",
            "LinkedIn-Version": API_VERSION,
            "X-Restli-Protocol-Version": "2.0.0",
        },
        timeout=15,
    )
    if not r.ok:
        return {}
    data = r.json()
    return {
        "likes":    data.get("likesSummary",    {}).get("totalLikes",                 0),
        "comments": data.get("commentsSummary", {}).get("totalFirstLevelComments",    0),
        "reposts":  data.get("repostsSummary",  {}).get("repostsCount",               0),
    }


def post_to_linkedin(user_id: str, text: str) -> dict:
    access_token = get_token(user_id)
    if not access_token:
        raise ValueError("LinkedIn not connected")
    person_id = _get_person_id(user_id)

    payload = {
        "author": f"urn:li:person:{person_id}",
        "lifecycleState": "PUBLISHED",
        "specificContent": {
            "com.linkedin.ugc.ShareContent": {
                "shareCommentary": {"text": text},
                "shareMediaCategory": "NONE",
            }
        },
        "visibility": {
            "com.linkedin.ugc.MemberNetworkVisibility": "PUBLIC"
        },
    }

    response = requests.post(
        "https://api.linkedin.com/v2/ugcPosts",
        json=payload,
        headers={
            "Authorization": f"Bearer {access_token}",
            "X-Restli-Protocol-Version": "2.0.0",
            "Content-Type": "application/json",
        },
        timeout=15,
    )
    if not response.ok:
        # ValueError so the API layer passes the specific reason through instead
        # of replacing every failure with "please reconnect LinkedIn".
        raise ValueError(describe_api_failure(response.status_code, response.text))
    return {"status": "posted", "id": response.headers.get("x-restli-id", "")}
