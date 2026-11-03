import base64
import os
import time
from datetime import datetime
from dotenv import load_dotenv
import httpx
import db
import partners

load_dotenv()

# Accept either name — root .env uses NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY,
# backend/.env uses CLERK_PUBLISHABLE_KEY. Either resolves the same Clerk domain.
CLERK_PUBLISHABLE_KEY = os.getenv("CLERK_PUBLISHABLE_KEY") or os.getenv("NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY", "")
CLERK_SECRET_KEY      = os.getenv("CLERK_SECRET_KEY", "")
_ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}

# ── JWKS cache per Clerk domain (refresh every hour) ─────────────────────────
# Voyce and each partner have their own Clerk application, so their own signing keys.
_jwks: dict = {}          # domain -> (keys, fetched_at)
_JWKS_TTL = 3600


def _clerk_domain(publishable_key: str = "") -> str:
    try:
        encoded = (publishable_key or CLERK_PUBLISHABLE_KEY).split("_", 2)[2]
        padding = (4 - len(encoded) % 4) % 4
        return base64.b64decode(encoded + "=" * padding).decode().rstrip("$")
    except Exception:
        return ""


def _get_jwks(domain: str, force: bool = False) -> dict:
    now = time.time()
    keys, fetched = _jwks.get(domain, ({}, 0.0))
    if keys and not force and (now - fetched) < _JWKS_TTL:
        return keys
    if not domain:
        return {}
    try:
        resp = httpx.get(f"https://{domain}/.well-known/jwks.json", timeout=5)
        resp.raise_for_status()
        keys = resp.json()
        _jwks[domain] = (keys, now)
    except Exception:
        pass  # return stale on failure
    return keys


def _verify_jwt(token: str, publishable_key: str = "") -> dict | None:
    """The token's claims when it was signed by the Clerk application behind publishable_key (Voyce's
    when empty). A token from another application fails here, so a Voyce session never opens a
    partner's app, nor the other way round."""
    from jose import jwt, JWTError
    try:
        domain = _clerk_domain(publishable_key)
        jwks = _get_jwks(domain)
        if not jwks:
            return None
        header = jwt.get_unverified_header(token)
        kid = header.get("kid")
        key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
        if not key:
            jwks = _get_jwks(domain, force=True)   # keys may have rotated
            key = next((k for k in jwks.get("keys", []) if k.get("kid") == kid), None)
            if not key:
                return None
        return jwt.decode(token, key, algorithms=["RS256"])
    except (JWTError, Exception):
        return None


def _fetch_clerk_user(clerk_id: str, secret_key: str = "") -> dict:
    try:
        resp = httpx.get(
            f"https://api.clerk.dev/v1/users/{clerk_id}",
            headers={"Authorization": f"Bearer {secret_key or CLERK_SECRET_KEY}"},
            timeout=5,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception:
        return {}


def get_user_by_token(token: str) -> dict | None:
    """The account a session token belongs to, checked against the sign-in of the address the request came
    in on (partners.current()). A partner's address only ever finds that partner's accounts, and
    voyce.co.in only Voyce's."""
    if not token:
        return None
    partner = partners.current()
    publishable, secret = partners.clerk_keys(partner) if partner else ("", "")
    if partner and not publishable:
        return None   # this partner's sign-in isn't set up yet
    claims = _verify_jwt(token, publishable)
    if not claims:
        return None

    clerk_id = claims.get("sub", "")
    if not clerk_id:
        return None

    user = db.users.find_one({"clerk_id": clerk_id}, {"_id": 0})
    if user:
        if (user.get("partner_id") or None) != (partner["id"] if partner else None):
            return None
        return user

    # First login — fetch profile from Clerk and create our record
    cu = _fetch_clerk_user(clerk_id, secret)
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
    if partner:
        # A partner's client: no plan of their own. They write once a seat is theirs (an invite from the
        # partner's team, accepted at /partner/join); the team's own members are recognised by email.
        user.update(partner_id=partner["id"], plan="partner", seat="none")
    db.users.insert_one({**user, "_id": clerk_id})
    return user


def update_account_type(user_id: str, account_type: str):
    db.users.update_one({"id": user_id}, {"$set": {"account_type": account_type}})


def _partner_gen_info(user: dict, partner: dict) -> dict:
    """A partner seat: drafts and AI pictures a month, refilled on the first. Counted from the account's
    totals at the start of the month, the same way a pass counts from its purchase. No seat, nothing."""
    month = partners.month_key()
    used_total, pics_total = int(user.get("gens_used", 0) or 0), int(user.get("ai_pics_used", 0) or 0)
    if user.get("seat_month") != month:
        db.users.update_one({"id": user["id"], "seat_month": {"$ne": month}},
                            {"$set": {"seat_month": month, "seat_gens_start": used_total, "seat_ai_start": pics_total}})
        user = {**user, "seat_month": month, "seat_gens_start": used_total, "seat_ai_start": pics_total}
    active = user.get("seat") == "active"
    return {"used": max(0, used_total - int(user.get("seat_gens_start", used_total) or 0)),
            "limit": partner["drafts_per_seat"] if active else 0,
            "plan": "partner", "partner": partner["id"], "seat": user.get("seat", "none"),
            "ai_used": max(0, pics_total - int(user.get("seat_ai_start", pics_total) or 0)),
            "ai_limit": partner["ai_pictures_per_seat"] if active else 0,
            "refills_at": partners.next_month_start().isoformat()}


def get_gen_info(user_id: str) -> dict:
    user = db.users.find_one({"id": user_id}, {"_id": 0}) or {}
    partner = partners.for_user(user)
    if partner:
        return _partner_gen_info(user, partner)
    if user.get("email", "").lower() in _ADMIN_EMAILS:
        return {"used": 0, "limit": -1, "plan": "admin", "ai_used": 0, "ai_limit": -1}
    plan       = user.get("plan", "free")
    expires_at = user.get("plan_expires_at")
    if plan == "pro" and expires_at and expires_at < datetime.now().isoformat():
        # Pro period ended — lazily downgrade back to free
        plan = "free"
        db.users.update_one({"id": user_id}, {"$set": {"plan": "free"}})
    used  = user.get("gens_used", 0)
    limit = 5 if plan == "free" else -1
    region = user.get("plan_region", "INTL")
    if plan == "pro" and region == "IN":
        # An India pass includes a fixed number of generations, counted from the
        # moment it was bought (see payments.activate_pro).
        from payments import INDIA_PASS_GENERATIONS
        start = int(user.get("pass_gens_start", used) or 0)
        used  = max(0, used - start)
        allowance = user.get("pass_gens_allowance")   # 0 is a real value, not "unset"
        limit = int(allowance) if allowance is not None else INDIA_PASS_GENERATIONS
    info = {"used": used, "limit": limit, "plan": plan}
    if plan == "pro" and expires_at:
        info["expires_at"] = expires_at
    if plan == "pro":
        info["plan_region"] = region   # "IN": 1 daily-posting profile, capped generations
    # AI pictures: a pass counts them from its purchase (a pass bought before they were counted
    # starts from none used); a free account counts them for good
    from payments import INDIA_PASS_AI_PICTURES, INTL_PASS_AI_PICTURES, FREE_AI_PICTURES
    pics = int(user.get("ai_pics_used", 0) or 0)
    if plan == "pro":
        info["ai_used"] = max(0, pics - int(user.get("pass_ai_start", 0) or 0))
        allowance = user.get("pass_ai_allowance")
        info["ai_limit"] = int(allowance) if allowance is not None else (
            INDIA_PASS_AI_PICTURES if region == "IN" else INTL_PASS_AI_PICTURES)
    else:
        info["ai_used"], info["ai_limit"] = pics, FREE_AI_PICTURES
    return info


def increment_gens(user_id: str):
    db.users.update_one({"id": user_id}, {"$inc": {"gens_used": 1}})


def increment_ai_pics(user_id: str):
    db.users.update_one({"id": user_id}, {"$inc": {"ai_pics_used": 1}})
