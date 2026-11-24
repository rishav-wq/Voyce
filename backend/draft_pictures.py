"""A waiting draft's picture, changed on its own (24 Nov 2026: Thahseen asked for photo uploads inside the app; Rishav
asked how a picture-only change on the email review page fits the post). The post's text is the master: a new picture
is drawn from it, or is the author's own photo or upload, so the words never have to change to fit. The one exception
is a carousel turned into a single picture: its lines about swiping point at nothing and come out.
Used by the Queue (signed in: /pending/{id}/picture) and the email review page (its link: main.picture_from_link)."""
import base64
import io
import re

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import auth as auth_module
import db
import notes      # its sign-in check
import ratelimit

router = APIRouter()
MODES = ("make", "photo", "upload", "none")
# A carousel caption's lines about the slides ("Swipe through…", "Save this carousel") once it's one picture
_SLIDES_LINE = re.compile(r"\bswip(e|ing)\b|\bcarousel\b|\b(each|every|these|the) slides?\b|\bslides? (below|ahead)\b", re.I)


def drop_slide_lines(text: str) -> str:
    kept = [line for line in (text or "").splitlines() if not _SLIDES_LINE.search(line)]
    out = re.sub(r"\n{3,}", "\n\n", "\n".join(kept)).strip()
    return out or text


def _jpeg(im) -> bytes:
    buf = io.BytesIO()
    im.convert("RGB").save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def _picture(pend: dict, company: dict, mode: str, photo_id: str = "", upload_b64: str = "") -> tuple[bytes, str, str]:
    """(image bytes, picture_format, alt text) for the draft."""
    from PIL import Image
    import author_photos
    import autonomous
    import carousel as C
    text = pend.get("post_text") or ""
    first = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")[:200] or "post picture"
    if mode == "photo":
        doc = db.author_photos.find_one({"company_id": company["id"], "id": photo_id}, {"_id": 0, "image": 1})
        if not doc:
            raise HTTPException(status_code=404, detail="That photo isn't in your photos any more.")
        return _jpeg(C._portrait_crop(Image.open(io.BytesIO(base64.b64decode(doc["image"]))))), "your_photo", first
    if mode == "upload":
        try:
            im = author_photos._decode(upload_b64)
        except Exception as e:
            raise HTTPException(status_code=400, detail={
                "too large": "That picture is too large: use one under 12 MB.",
                "too small": "That picture is too small: use one at least 400 pixels across.",
            }.get(str(e), "That file isn't a picture we can read."))
        return _jpeg(im), "upload", first
    # "make": drawn from the post the way the daily picture is (its job, no repeat of the last one, the weekly AI cap).
    # With the plan's AI pictures used up, the post's line on a handwritten note.
    info = auth_module.get_gen_info(pend.get("user_id", ""))
    ai_left = info.get("ai_limit", 0) == -1 or info.get("ai_used", 0) < info.get("ai_limit", 0)
    pic = autonomous._daily_picture(text, company) if ai_left else None
    if pic:
        if pic.get("format") in autonomous._AI_DRAWN:
            auth_module.increment_ai_pics(pend.get("user_id", ""))
        return pic["png"], pic.get("format", ""), pic.get("alt") or first
    png = autonomous._tweet_card_for(text, company)
    if not png:
        raise HTTPException(status_code=502, detail="Couldn't draw a picture for this post just now. Try again in a minute.")
    return png, "note_card", first


def apply(pend: dict, mode: str, photo_id: str = "", upload_b64: str = "") -> dict:
    """Change the waiting draft's picture; what the page needs to show it."""
    if mode not in MODES:
        raise HTTPException(status_code=400, detail="Pick a picture first.")
    if (pend or {}).get("status") != "pending":
        raise HTTPException(status_code=409, detail="This post isn't waiting for approval any more.")
    if pend.get("format") == "diy_video":
        raise HTTPException(status_code=400, detail="A video day has no picture to change.")
    company = db.companies.find_one({"id": pend.get("company_id", "")}, {"_id": 0})
    if not company:
        raise HTTPException(status_code=404, detail="Not found")
    text = pend.get("post_text") or ""
    upd: dict = {}
    if pend.get("format") == "carousel":
        new_text = drop_slide_lines(text)
        if new_text != text:
            upd.update(post_text=new_text, publish_text=new_text)
            text = new_text
    if mode == "none":
        upd.update(format="text", asset_b64="", slide_previews=[], alt_text="", picture_format="")
    else:
        from PIL import Image
        from carousel import slide_previews
        img, pic_fmt, alt = _picture({**pend, "post_text": text}, company, mode, photo_id, upload_b64)
        upd.update(format="tweet_card", asset_b64=base64.b64encode(img).decode(), alt_text=alt, picture_format=pic_fmt,
                   slide_previews=slide_previews([Image.open(io.BytesIO(img))]))
    if not db.pending_posts.update_one({"id": pend["id"], "status": "pending"}, {"$set": upd}).matched_count:
        raise HTTPException(status_code=409, detail="This post isn't waiting for approval any more.")
    return {"format": upd["format"], "slide_previews": upd.get("slide_previews", []), "post_text": text,
            "picture_format": upd.get("picture_format", "")}


class PictureIn(BaseModel):
    mode: str
    photo_id: str = ""
    image_base64: str = ""


@router.post("/pending/{pending_id}/picture")
def change_picture(pending_id: str, body: PictureIn, x_token: str = Header(None)):
    user = notes._user(x_token)
    if not ratelimit.allow(f"draftpic:{user['id']}", 20, 3600):
        notes._too_fast()
    pend = db.pending_posts.find_one({"id": pending_id, "user_id": user["id"]}, {"_id": 0})
    if not pend:
        raise HTTPException(status_code=404, detail="Not found")
    return apply(pend, body.mode, body.photo_id, body.image_base64)
