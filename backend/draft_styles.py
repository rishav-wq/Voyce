"""Try another style on a waiting draft (24 Nov 2026, Thahseen: "at the time of approval, I need a regeneration with
different styles, especially the tone, it's very flat"). The same draft, rewritten as More story, Bolder, Warmer or
Shorter: same facts, different telling. Every number in a new version must already be in the draft or in what the
author gave Voyce; otherwise it's written once more, then refused. The first version is kept, so "Back to the first
version" is one tap away. A restyle counts against the day's rewrites (main.DAILY_LIMITS), never against posts.
Used from the Queue (POST /pending/{id}/style) and the approval email's page (main: POST /approve/{token}/style)."""
import re

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import db
import notes      # its sign-in check
import ratelimit

router = APIRouter()

STYLES = {
    "story": ("More story",
              "Tell it as a story: open inside the moment the draft describes, then the turning point, then the lesson. "
              "Use only moments and details that are already in the draft or the author context. If there is no "
              "scene, make the order more story-like without inventing one."),
    "bolder": ("Bolder",
               "Give it a sharper point of view: a stronger first line, fewer hedges, short punchy lines mixed with one "
               "or two longer ones, and a closing line that lands hard. Never exaggerate a fact to sound bolder."),
    "warmer": ("Warmer",
               "Make it warmer and more personal, like telling a colleague over coffee: first person, contractions, "
               "plain words. Say how it felt or what it meant only if the draft or the author context says so. "
               "No motivational tone."),
    "shorter": ("Shorter",
                "Cut it to the strongest 90 to 130 words: keep the hook, the key moment or number, and the lesson. "
                "Drop everything else."),
}
_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUM.findall(text or "")}


def added_numbers(new: str, allowed: str) -> list[str]:
    """Numbers the new version states that appear nowhere in the draft or the author's material."""
    ok = _numbers(allowed)
    return sorted(n for n in _numbers(new) if n not in ok)


def restyle_text(company: dict, base_text: str, style: str) -> str:
    """The draft in another style, facts unchanged. Raises 422 when it can't be done without adding numbers."""
    import autonomous
    from llm import generate as llm_generate
    instruction = STYLES[style][1]
    brief = autonomous._build_company_brief(company)
    prompt = f"""You are rewriting a LinkedIn post for {company.get('name') or 'the author'}.

AUTHOR CONTEXT (their voice, their own writing rules and their real facts):
{brief}

THE DRAFT:
\"\"\"{base_text[:3000]}\"\"\"

REWRITE IT: {instruction}

Rules:
- Keep every fact, number, name and claim exactly as the draft states it. Add no new facts, numbers, events, quotes,
  clients or feelings that are not in the draft or the author context.
- Keep the author's voice and follow their own writing rules.
- First line: 8 to 10 words, never a question. No engagement bait ("Comment X", "DM me", "Agree?").
- Keep the draft's hashtags at the end if it has them, and add none.
- Plain text with a blank line between paragraphs.
Return ONLY the rewritten post."""
    allowed = base_text + "\n" + autonomous._author_record(company)
    extra = ""
    for _ in range(2):
        out = autonomous._format_linkedin_post(llm_generate(prompt + extra, max_tokens=1024, temperature=0.8) or "").strip()
        added = added_numbers(out, allowed)
        if out and not added:
            return out
        extra = ("\n\nYOUR LAST VERSION ADDED NUMBERS THAT ARE NOT IN THE DRAFT: " + ", ".join(added[:6])
                 + ". Write it again without them.") if added else ""
    raise HTTPException(status_code=422, detail="Couldn't rewrite it without adding things you didn't say. Try another style.")


def apply(pend: dict, style: str, base_text: str = "") -> dict:
    """Rewrite a waiting draft in a style ("first" brings back the version Voyce wrote) and save it."""
    if style != "first" and style not in STYLES:
        raise HTTPException(status_code=400, detail="Unknown style.")
    if pend.get("status") != "pending":
        raise HTTPException(status_code=409, detail="This post isn't waiting for approval any more.")
    first = pend.get("first_text") or pend.get("post_text") or ""
    if style == "first":
        text, label = first, ""
    else:
        user = db.users.find_one({"id": pend.get("user_id", "")}, {"_id": 0}) or {"id": pend.get("user_id", "")}
        from main import _daily_cap       # loaded by now; importing it at the top would be circular
        _daily_cap(user, "rewrites")
        company = db.companies.find_one({"id": pend.get("company_id", "")}, {"_id": 0}) or {}
        text = restyle_text(company, (base_text or pend.get("post_text") or "").strip(), style)
        label = STYLES[style][0]
    r = db.pending_posts.update_one({"id": pend["id"], "status": "pending"},
                                    {"$set": {"post_text": text, "publish_text": text, "first_text": first,
                                              "style": label}})
    if not r.matched_count:
        raise HTTPException(status_code=409, detail="This post isn't waiting for approval any more.")
    return {"post_text": text, "style": label, "has_first": text != first}


class StyleIn(BaseModel):
    style: str
    text: str = ""     # the version on screen, if they edited it before asking


@router.post("/pending/{pending_id}/style")
def restyle(pending_id: str, body: StyleIn, x_token: str = Header(None)):
    user = notes._user(x_token)
    if not ratelimit.allow(f"draftstyle:{user['id']}", 20, 3600):
        notes._too_fast()
    pend = db.pending_posts.find_one({"id": pending_id, "user_id": user["id"]}, {"_id": 0})
    if not pend:
        raise HTTPException(status_code=404, detail="Not found")
    return apply(pend, body.style, body.text[:4000])
