import base64
import os
import time
from datetime import datetime
from dotenv import load_dotenv
import httpx
import db

load_dotenv()

# Accept either name — root .env uses NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
# backend/.env uses CLERK_PUBLISHABLE_KEY. Either resolves the same Clerk domain.
CLERK_PUBLISHABLE_KEY = os.getenv("CLERK_PUBLISHABLE_KEY") or os.getenv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "")
CLERK_SECRET_KEY      = os.getenv("CLERK_SECRET_KEY", "")
_ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}

# ── JWKS cache (refresh every hour) ──────────────────────────────────────────
_jwks: dict = {}
_jwks_fetched_at: float = 0.0
_JWKS_TTL = 3600


def _clerk_domain() -> str:
    try:
        encoded = CLERK_PUBLISHABLE_KEY.split("_", 2)[2]
        padding = (4 - len(encoded) % 4) % 4
        return base64.b64decode(encoded + "=" * padding).decode().rstrip("$")
    except Exception:
        return ""


def _get_jwks() -> dict:
    global _jwks, _jwks_fetched_at
    now = time.time()
    if _jwks and (now - _jwks_fetched_at) < _JWKS_TTL:
        return _jwks
    domain = _clerk_domain()
    if not domain:
        return {}
    try:
        resp = httpx.get(f"https://{domain}/.well-known/jwks.json", timeout=5)
        resp.raise_for_status()
        _jwks = resp.json()
        _jwks_fetched_at = now
    except Exception:
        pass  # return stale on failure
    return _jwks


def _verify_jwt(token: str) -> dict | None:
    from jose import jwt, JWTError
    try:
        jwks = _get_jwks()
        if not jwks:
            return None
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if not key:
            # Keys may have rotated — force refresh once
            global _jwks_fetched_at
            _jwks_fetched_at = 0
            jwks = _get_jwks()
            key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
            if not key:
                return None
        return jwt.decode(token, key, algorithms=["RS256"])
    except (JWTError, Exception):
        return None


def _fetch_clerk_user(clerk_id: str) -> dict:
    try:
        resp = httpx.get(
            f"https://api.clerk.dev/v1/users/{clerk_id}",
            headers={"Authorization": f"Bearer {CLERK_SECRET_KEY}"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def get_user_by_token(token: str) -> dict | None:
    if not token:
        return None
    claims = _verify_jwt(token)
    if not claims:
        return None

    clerk_id = claims.get("sub", "")
    if not clerk_id:
        return None

    user = db.users.find_one({"clerk_id": clerk_id}, {"_id": 0})
    if user:
        return user

    # First login — fetch profile from Clerk and create our record
    cu = _fetch_clerk_user(clerk_id)
    email = ""
    if cu.get("primary_email_address_id") and cu.get("email_addresses"):
        peid = cu["primary_email_address_id"]
        ea = next((e for e in cu["email_addresses"] if e["id"] == peid), None)
        if ea:
            email = ea["email_address"].lower()
    name = f"{cu.get('first_name') or ''} {cu.get('last_name') or ''}".strip() or email.split("@")[0] or "User"

    user = {
        "id":           clerk_id,
        "clerk_id":     clerk_id,
        "email":        email,
        "name":         name,
        "account_type": None,
        "created_at":   datetime.now().isoformat(),
        "plan":         "free",
        "gens_used":    0,
    }
    db.users.insert_one({**user, "_id": clerk_id})
    return user


def update_account_type(user_id: str, account_type: str):
    db.users.update_one({"id": user_id}, {"$set": {"account_type": account_type}})


def get_gen_info(user_id: str) -> dict:
    user = db.users.find_one({"id": user_id}, {"_id": 0}) or {}
    if user.get("email", "").lower() in _ADMIN_EMAILS:
        return {"used": 0, "limit": -1, "plan": "admin"}
    plan       = user.get("plan", "free")
    expires_at = user.get("plan_expires_at")
    if plan == "pro" and expires_at and expires_at < datetime.now().isoformat():
        # Pro period ended — lazily downgrade back to free
        plan = "free"
        db.users.update_one({"id": user_id}, {"$set": {"plan": "free"}})
    used  = user.get("gens_used", 0)
    limit = 5 if plan == "free" else -1
    info = {"used": used, "limit": limit, "plan": plan}
    if plan == "pro" and expires_at:
        info["expires_at"] = expires_at
    return info


def increment_gens(user_id: str):
    db.users.update_one({"id": user_id}, {"$inc": {"gens_used": 1}})
