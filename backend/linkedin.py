import os
import base64
import json
import requests
from dotenv import load_dotenv

import db

load_dotenv()

CLIENT_ID = os.getenv("LINKEDIN_CLIENT_ID")
CLIENT_SECRET = os.getenv("LINKEDIN_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "http://localhost:8000/auth/linkedin/callback")
SCOPES = "openid profile w_member_social"

# OAuth state -> user_id mapping (short-lived, in-memory is fine)
_oauth_states: dict[str, str] = {}


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


def register_state(state: str, user_id: str):
    _oauth_states[state] = user_id


def consume_state(state: str) -> str | None:
    return _oauth_states.pop(state, None)


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
    )
    response.raise_for_status()
    return response.json()


def _decode_id_token(id_token: str) -> dict:
    payload = id_token.split('.')[1]
    payload += '=' * (4 - len(payload) % 4)
    return json.loads(base64.b64decode(payload))


def save_token(user_id: str, token_data: dict):
    entry = {"access_token": token_data["access_token"]}
    if "id_token" in token_data:
        claims = _decode_id_token(token_data["id_token"])
        entry["person_id"] = claims.get("sub", "")
    _save_token_entry(user_id, entry)


def get_token(user_id: str) -> str | None:
    return _get_token_entry(user_id).get("access_token")


def is_connected(user_id: str) -> bool:
    return bool(_get_token_entry(user_id).get("access_token"))


def logout(user_id: str):
    db.li_tokens.delete_one({"user_id": user_id})


def _get_person_id(user_id: str) -> str:
    person_id = _get_token_entry(user_id).get("person_id", "")
    if not person_id:
        raise ValueError("Person ID not found. Please reconnect LinkedIn.")
    return person_id


def upload_and_post_carousel(user_id: str, pdf_bytes: bytes, post_text: str, title: str = "Carousel") -> dict:
    access_token = get_token(user_id)
    if not access_token:
        raise ValueError("LinkedIn not connected")
    person_id = _get_person_id(user_id)
    headers_base = {
        "Authorization": f"Bearer {access_token}",
        "LinkedIn-Version": "202503",
        "X-Restli-Protocol-Version": "2.0.0",
    }

    # Step 1: Initialize upload
    init_res = requests.post(
        "https://api.linkedin.com/rest/documents?action=initializeUpload",
        json={"initializeUploadRequest": {"owner": f"urn:li:person:{person_id}"}},
        headers={**headers_base, "Content-Type": "application/json"},
    )
    if not init_res.ok:
        raise ValueError(f"LinkedIn document init failed {init_res.status_code}: {init_res.text}")
    init_res.raise_for_status()
    val = init_res.json()["value"]
    upload_url   = val["uploadUrl"]
    document_urn = val["document"]

    # Step 2: Upload PDF binary
    requests.put(upload_url, data=pdf_bytes,
                 headers={"Content-Type": "application/octet-stream"}).raise_for_status()

    # Step 3: Create document post
    payload = {
        "author": f"urn:li:person:{person_id}",
        "commentary": post_text,
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
    )
    if not post_res.ok:
        raise ValueError(f"LinkedIn post failed {post_res.status_code}: {post_res.text}")
    post_res.raise_for_status()
    return {"status": "posted", "type": "carousel", "id": post_res.headers.get("x-restli-id", "")}


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
            "LinkedIn-Version": "202503",
            "X-Restli-Protocol-Version": "2.0.0",
        },
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
    )
    response.raise_for_status()
    return {"status": "posted", "id": response.headers.get("x-restli-id", "")}
