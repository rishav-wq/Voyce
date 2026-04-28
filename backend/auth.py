import hashlib
import os
import secrets
from datetime import datetime
from dotenv import load_dotenv

import db

load_dotenv()
_ADMIN_EMAILS = {e.strip().lower() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()}

_P = {"_id": 0}


def _hash(password: str, salt: str) -> str:
    return hashlib.sha256(f"{salt}{password}".encode()).hexdigest()


def _safe(user: dict) -> dict:
    return {k: v for k, v in user.items() if k not in ("password_hash", "salt")}


def register(email: str, password: str, name: str, account_type: str) -> dict:
    email = email.strip().lower()
    if db.users.find_one({"email": email}, _P):
        raise ValueError("Email already registered")

    user_id = secrets.token_urlsafe(12)
    salt    = secrets.token_hex(16)
    user = {
        "id":           user_id,
        "email":        email,
        "name":         name,
        "account_type": account_type,
        "password_hash": _hash(password, salt),
        "salt":         salt,
        "created_at":   datetime.now().isoformat(),
        "plan":         "free",
        "gens_used":    0,
    }
    db.users.insert_one({**user, "_id": user_id})
    return _safe(user)


def login(email: str, password: str) -> str:
    email = email.strip().lower()
    user = db.users.find_one({"email": email})
    if not user or user["password_hash"] != _hash(password, user["salt"]):
        raise ValueError("Invalid email or password")
    token = secrets.token_urlsafe(32)
    db.sessions.replace_one({"_id": token}, {"_id": token, "user_id": user["id"]}, upsert=True)
    return token


def get_user_by_token(token: str) -> dict | None:
    if not token:
        return None
    sess = db.sessions.find_one({"_id": token})
    if not sess:
        return None
    user = db.users.find_one({"id": sess["user_id"]}, _P)
    return _safe(user) if user else None


def logout(token: str):
    db.sessions.delete_one({"_id": token})


def get_gen_info(user_id: str) -> dict:
    user = db.users.find_one({"id": user_id}, _P) or {}
    if user.get("email", "").lower() in _ADMIN_EMAILS:
        return {"used": 0, "limit": -1, "plan": "admin"}
    plan  = user.get("plan", "free")
    used  = user.get("gens_used", 0)
    limit = 7 if plan == "free" else -1
    return {"used": used, "limit": limit, "plan": plan}


def increment_gens(user_id: str):
    db.users.update_one({"id": user_id}, {"$inc": {"gens_used": 1}})
