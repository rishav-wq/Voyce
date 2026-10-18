"""What Voyce knows about an author: notes only they could give (a real experience, a step
they always take, a number from their work, an opinion they argue), so daily posts carry
something no other AI post has.

Notes come from a weekly question (answered in Queue, or on the approve page straight from
the approval email), from notes the author adds, or from a talk or podcast of theirs (Voyce
reads the transcript). Each daily post is built around one note, rotating so none repeats
soon; the invented-history check treats every note as the author's real past.

Everything here is optional: an author who never answers gets the same news-led posts as
before. Questions follow LinkedIn ghostwriters' interview practice: ask about a recent
moment, never "what do you want to write about".
"""
import hashlib
import re
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import auth as auth_module
import db
import ratelimit

router = APIRouter()

KINDS = {"experience": "A real experience", "process": "How they do it", "data": "A number from their work",
         "opinion": "An opinion", "other": "Something else"}
# Interleaved by kind so consecutive weeks never ask the same sort of thing.
QUESTIONS = [
    ("experience", "What decision took up most of your week, and what made it hard?"),
    ("opinion", "What's something your industry is getting wrong right now?"),
    ("process", "What's a step you always take that most people in your field skip?"),
    ("data", "What number are you watching closely at work right now, and why?"),
    ("experience", "What did a client or colleague say recently that surprised you?"),
    ("opinion", "What common advice sounds good but fails in practice?"),
    ("process", "What question keeps coming up with clients, and how do you answer it?"),
    ("data", "What's a recent result you can share: a before and after, a deal, a metric?"),
    ("experience", "What have you learned the hard way this month?"),
    ("opinion", "What belief about your work have you changed your mind on?"),
    ("process", "What advice do you find yourself repeating to people?"),
    ("experience", "What's a mistake you've seen (or made) lately that others could learn from?"),
]
MAX_NOTES = 80
MIN_LEN, MAX_LEN = 12, 1500


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def week_key(when: datetime | None = None) -> str:
    iso = (when or datetime.now(timezone.utc)).isocalendar()
    return f"{iso[0]}-W{iso[1]:02d}"


def question_for(company: dict, when: datetime | None = None) -> dict:
    """This week's question for a profile, and whether it was answered or skipped."""
    wk = week_key(when)
    iso_week = int(wk.split("W")[1])
    offset = int(hashlib.md5(str(company.get("id", "")).encode()).hexdigest()[:4], 16)
    kind, text = QUESTIONS[(iso_week + offset) % len(QUESTIONS)]
    state = company.get("weekly_q") or {}
    status = state.get("status", "") if state.get("week") == wk else ""
    return {"week": wk, "kind": kind, "text": text, "status": status}


def _owned(company_id: str, user_id: str) -> dict:
    c = db.companies.find_one({"id": company_id}, {"_id": 0})
    if not c or c.get("user_id") != user_id:
        raise HTTPException(status_code=404, detail="Not found")
    return c


def _clean_text(text: str) -> str:
    text = re.sub(r"[ \t]+", " ", str(text or "")).strip()
    if len(text) < MIN_LEN:
        raise HTTPException(status_code=400, detail="Write a sentence or two, so there's something to build a post on.")
    return text[:MAX_LEN]


def add(company: dict, text: str, kind: str = "other", source: str = "added", question: str = "") -> dict:
    """Save one note on a profile. Answering this week's question marks the week answered."""
    notes = list(company.get("author_notes") or [])
    if len(notes) >= MAX_NOTES:
        raise HTTPException(status_code=400, detail=f"A profile holds up to {MAX_NOTES} notes. Delete some old ones first.")
    note = {"id": "n_" + secrets.token_urlsafe(6), "text": _clean_text(text), "kind": kind if kind in KINDS else "other",
            "source": source if source in ("weekly", "added", "talk", "email") else "added",
            "question": str(question or "")[:200], "created_at": _now(), "uses": 0, "used_at": ""}
    update = {"$push": {"author_notes": note}}
    if source in ("weekly", "email"):
        update["$set"] = {"weekly_q": {"week": week_key(), "status": "answered"}}
    db.companies.update_one({"id": company["id"]}, update)
    return note


def pick(company: dict) -> dict | None:
    """The note today's post is built around: the least used, then the longest unused."""
    notes = company.get("author_notes") or []
    if not notes:
        return None
    return sorted(notes, key=lambda n: (int(n.get("uses") or 0), n.get("used_at") or "", n.get("created_at") or ""))[0]


def mark_used(company_id: str, note_id: str) -> None:
    try:
        c = db.companies.find_one({"id": company_id}, {"_id": 0, "author_notes": 1}) or {}
        notes = c.get("author_notes") or []
        for n in notes:
            if n.get("id") == note_id:
                n["uses"] = int(n.get("uses") or 0) + 1
                n["used_at"] = _now()
        db.companies.update_one({"id": company_id}, {"$set": {"author_notes": notes}})
    except Exception:
        pass   # rotation is a nicety; never break a post over it


def record_block(company: dict) -> str:
    """For the writer's author context and the invented-history check: the author's own notes."""
    notes = company.get("author_notes") or []
    if not notes:
        return ""
    lines = [f"- ({KINDS.get(n.get('kind'), 'Note')}) {str(n.get('text', ''))[:500]}" for n in notes[-12:]]
    return "Things the author told Voyce, in their own words (real; use only what's said, never embellish):\n" + "\n".join(lines)


def prompt_block(note: dict) -> str:
    """Today's material: the post is built around this note, with the news as the hook."""
    return ("\n\nTODAY'S MATERIAL FROM THE AUTHOR (their own words, real):\n"
            f"\"{str(note.get('text', ''))[:600]}\"\n"
            "Build the post around this. Use today's news as the hook or the context where it genuinely "
            "connects; if it doesn't, write from this alone and leave the news out. Keep every detail exactly "
            "as they said it: don't add numbers, names, dates or events they didn't give.")


def extract_from_transcript(transcript: str, name: str) -> list[dict]:
    """Pull what only the speaker could say out of a talk or podcast transcript."""
    from llm import generate_json
    prompt = f"""Below is a transcript of a talk, podcast or interview where {name} speaks.

Pull out up to 8 things only {name} could say: a real experience or story, a method or steps
they use, a number from their own work, or an opinion they argue.

Rules:
- Use only what the speaker actually says. Never invent, embellish or generalise.
- Skip anything generic that any professional could say.
- If you can't tell which speaker is {name}, use only clear first-person statements.
- Write each as one or two plain sentences in the first person, the way {name} would say it.

TRANSCRIPT:
{transcript[:14000]}

Return ONLY JSON: {{"notes": [{{"kind": "experience|process|data|opinion", "text": "..."}}]}}"""
    data = generate_json(prompt, max_tokens=1400, temperature=0.2) or {}
    out = []
    for n in (data.get("notes") or [])[:8]:
        text = str(n.get("text") or "").strip()
        if len(text) >= MIN_LEN:
            out.append({"kind": n.get("kind") if n.get("kind") in KINDS else "other", "text": text[:MAX_LEN]})
    return out


# ── API ───────────────────────────────────────────────────────────────────────
def _user(x_token: str | None) -> dict:
    user = auth_module.get_user_by_token(x_token or "")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def _public(note: dict) -> dict:
    return {k: note.get(k) for k in ("id", "text", "kind", "source", "question", "created_at", "uses", "used_at")}


class NoteIn(BaseModel):
    text: str = ""
    kind: str = "other"
    source: str = "added"
    question: str = ""


class TalkIn(BaseModel):
    url: str = ""


@router.get("/notes/questions")
def questions(x_token: str = Header(None)):
    """This week's question for each of the user's profiles (for Queue)."""
    user = _user(x_token)
    out = []
    for c in db.companies.find({"user_id": user["id"]}, {"_id": 0, "id": 1, "name": 1, "weekly_q": 1}):
        out.append({"company_id": c["id"], "name": c.get("name", ""), **question_for(c)})
    return out


@router.get("/companies/{company_id}/notes")
def list_notes(company_id: str, x_token: str = Header(None)):
    user = _user(x_token)
    c = _owned(company_id, user["id"])
    return {"notes": [_public(n) for n in reversed(c.get("author_notes") or [])], "question": question_for(c)}


@router.post("/companies/{company_id}/notes")
def add_note(company_id: str, body: NoteIn, x_token: str = Header(None)):
    user = _user(x_token)
    ratelimit.allow(f"notes:{user['id']}", 30, 60) or _too_fast()
    c = _owned(company_id, user["id"])
    note = add(c, body.text, body.kind, body.source if body.source in ("weekly", "added") else "added", body.question)
    _track(user["id"], "note_added", {"source": note["source"], "kind": note["kind"]})
    return _public(note)


@router.delete("/companies/{company_id}/notes/{note_id}")
def delete_note(company_id: str, note_id: str, x_token: str = Header(None)):
    user = _user(x_token)
    c = _owned(company_id, user["id"])
    db.companies.update_one({"id": c["id"]}, {"$set": {"author_notes": [n for n in (c.get("author_notes") or []) if n.get("id") != note_id]}})
    return {"ok": True}


@router.post("/companies/{company_id}/notes/skip-week")
def skip_week(company_id: str, x_token: str = Header(None)):
    user = _user(x_token)
    c = _owned(company_id, user["id"])
    db.companies.update_one({"id": c["id"]}, {"$set": {"weekly_q": {"week": week_key(), "status": "skipped"}}})
    return {"ok": True}


@router.post("/companies/{company_id}/notes/from-talk")
def notes_from_talk(company_id: str, body: TalkIn, x_token: str = Header(None)):
    """A YouTube talk or podcast of theirs → notes, for them to review and keep or delete."""
    user = _user(x_token)
    ratelimit.allow(f"notes-talk:{user['id']}", 5, 3600) or _too_fast()
    c = _owned(company_id, user["id"])
    url = (body.url or "").strip()
    if not re.match(r"^https?://(www\.|m\.)?(youtube\.com|youtu\.be)/", url):
        raise HTTPException(status_code=400, detail="Paste a YouTube link to a talk, podcast or interview of yours.")
    from processor import process_input
    try:
        transcript = process_input("youtube", url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't read that video's transcript. Try another link.")
    try:
        found = extract_from_transcript(transcript, c.get("name") or "the speaker")
    except Exception:
        raise HTTPException(status_code=502, detail="Couldn't read the talk just now. Try again in a minute.")
    if not found:
        raise HTTPException(status_code=404, detail="Nothing in that talk sounded like only you could say it. Try another one.")
    room = MAX_NOTES - len(c.get("author_notes") or [])
    added = []
    for n in found[:max(0, room)]:
        note = {"id": "n_" + secrets.token_urlsafe(6), "text": n["text"], "kind": n["kind"], "source": "talk",
                "question": url[:200], "created_at": _now(), "uses": 0, "used_at": ""}
        added.append(note)
    if added:
        db.companies.update_one({"id": c["id"]}, {"$push": {"author_notes": {"$each": added}}})
    _track(user["id"], "notes_from_talk", {"found": len(added)})
    return {"added": [_public(n) for n in added]}


def _too_fast():
    raise HTTPException(status_code=429, detail="Too many at once. Wait a minute and try again.")


def _track(uid, event, props):
    try:
        import analytics
        analytics.capture(uid, event, props)
    except Exception:
        pass
