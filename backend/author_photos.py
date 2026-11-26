"""Photos of the author for the "Your photo" image (Nov 2026): their real face under the post's line, never an
AI-made one. MagicPost makes "AI photos of you" from selfies and Postiv puts the user's own photo behind a quote;
a consultant's own photo is the people-first picture without the risk of a made-up likeness.

Kept in their own collection (author_photos), not on the profile: /companies sends whole profiles to the app on
every load, and a few photos there would be megabytes each time. Each photo is resized to at most 1600 px on its
long side and stored as JPEG; a profile keeps up to MAX_PHOTOS, used in turn so posts don't repeat one.
"""
import base64
import os
import io
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import db
import notes   # its sign-in and ownership checks
import ratelimit

router = APIRouter()
MAX_PHOTOS = int(os.getenv("MAX_PHOTOS", "40") or 40)   # was 8 (26 Nov 2026: Vimal filled it on day one); a ceiling, not a target
MAX_BYTES = 12 * 1024 * 1024   # the upload as sent; what's stored is far smaller
LONG_SIDE = 1600


def _col():
    return db.author_photos


def count(company_id: str) -> int:
    if not company_id:
        return 0
    try:
        return _col().count_documents({"company_id": company_id})
    except Exception:
        return 0


def _decode(b64: str):
    from PIL import Image, ImageOps
    raw = base64.b64decode((b64 or "").split(",")[-1], validate=False)
    if not raw or len(raw) > MAX_BYTES:
        raise ValueError("too large")
    im = Image.open(io.BytesIO(raw))
    im = ImageOps.exif_transpose(im).convert("RGB")   # phone photos carry their rotation in EXIF
    if min(im.size) < 400:
        raise ValueError("too small")
    im.thumbnail((LONG_SIDE, LONG_SIDE), Image.LANCZOS)
    return im


def _jpeg(im, q: int = 86) -> bytes:
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=q, optimize=True)
    return buf.getvalue()


def add(company: dict, b64: str) -> dict:
    """Store one photo for a profile; its public form."""
    if count(company["id"]) >= MAX_PHOTOS:
        raise HTTPException(status_code=400, detail=f"A profile keeps up to {MAX_PHOTOS} photos. Remove one first.")
    try:
        im = _decode(b64)
    except Exception as e:
        msg = {"too large": "That photo is too large: use one under 12 MB.",
               "too small": "That photo is too small: use one at least 400 pixels across."}.get(str(e), "That file isn't a photo we can read.")
        raise HTTPException(status_code=400, detail=msg)
    doc = {"id": "ph_" + secrets.token_hex(5), "company_id": company["id"], "user_id": company.get("user_id", ""),
           "image": base64.b64encode(_jpeg(im)).decode(), "w": im.width, "h": im.height,
           "created_at": datetime.now(timezone.utc).isoformat(), "uses": 0, "used_at": ""}
    _col().insert_one(dict(doc))
    return _public(doc)


def _public(doc: dict, thumb: bool = True) -> dict:
    out = {k: doc.get(k) for k in ("id", "w", "h", "created_at", "uses")}
    if thumb and doc.get("image"):
        try:
            from PIL import Image
            im = Image.open(io.BytesIO(base64.b64decode(doc["image"])))
            im.thumbnail((320, 320))
            out["thumb"] = base64.b64encode(_jpeg(im, 80)).decode()
        except Exception:
            out["thumb"] = ""
    return out


def pick(company: dict | None):
    """The photo for the next picture, as a PIL image: the least used, then the longest unused. Marks it used.
    None when the profile has none."""
    cid = (company or {}).get("id")
    if not cid:
        return None
    try:
        docs = list(_col().find({"company_id": cid}, {"_id": 0}))
    except Exception:
        return None
    if not docs:
        return None
    doc = sorted(docs, key=lambda x: (int(x.get("uses") or 0), x.get("used_at") or "", x.get("created_at") or ""))[0]
    try:
        from PIL import Image
        im = Image.open(io.BytesIO(base64.b64decode(doc["image"]))).convert("RGB")
    except Exception:
        return None
    try:
        _col().update_one({"id": doc["id"]}, {"$inc": {"uses": 1},
                                              "$set": {"used_at": datetime.now(timezone.utc).isoformat()}})
    except Exception:
        pass
    return im


# ── API ───────────────────────────────────────────────────────────────────────
class PhotoIn(BaseModel):
    image_base64: str = ""


@router.get("/profiles/{company_id}/photos")
def list_photos(company_id: str, x_token: str = Header(None)):
    user = notes._user(x_token)
    notes._owned(company_id, user["id"])
    return [_public(d) for d in _col().find({"company_id": company_id}, {"_id": 0}).sort("created_at", 1)]


@router.post("/profiles/{company_id}/photos")
def add_photo(company_id: str, body: PhotoIn, x_token: str = Header(None)):
    user = notes._user(x_token)
    if not ratelimit.allow(f"photos:{user['id']}", 20, 3600):
        notes._too_fast()
    company = notes._owned(company_id, user["id"])
    return add(company, body.image_base64)


@router.delete("/profiles/{company_id}/photos/{photo_id}")
def delete_photo(company_id: str, photo_id: str, x_token: str = Header(None)):
    user = notes._user(x_token)
    notes._owned(company_id, user["id"])
    res = _col().delete_one({"company_id": company_id, "id": photo_id})
    if not res.deleted_count:
        raise HTTPException(status_code=404, detail="Not found")
    return {"ok": True}
