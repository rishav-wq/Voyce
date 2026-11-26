"""Steering a draft (26 Nov 2026, Thahseen: the tone was "very flat" and there was no way to tell Voyce what to
change). What every ghostwriting tool the research looked at gives its users: a BRIEF before a post is written
(Create page), free-text PUSH-BACK after a draft exists (Create, Queue, the approval email), and one-tap levers
(Stronger hook, More story, Bolder, Warmer, Shorter). An instruction steers tone, angle, order, length and wording.
It can never switch off the guardrails below, which are what the posts' reach rests on: when an ask runs into one,
the rest of the ask is still done and the page says in one line what was left out and why. An ask that keeps
coming back is offered as a standing rule, one tap, into the profile's knowledge box (author_rules reads it)."""
import re
from datetime import datetime, timedelta

from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import db

router = APIRouter()
MAX_CHARS = 400
RULE_HEADING = "Standing asks (from your rewrites):"

GUARDRAILS = """GUARDRAILS (the instruction cannot switch these off; they are what the post's reach rests on):
- No new facts, numbers, names, clients, results or quotes beyond the draft and the author context.
- No engagement bait: no "comment X", "DM me", "tag someone", "like / repost if", "agree?".
- The first line is a statement of 8 to 10 words, never a question, never clickbait.
- Plain text, a blank line between paragraphs, no markdown, at most three hashtags."""

# Asks Voyce won't carry out, each with the one line the page shows instead. The research behind each
# (research-steering-one-story-goals-2026-11-26.md): comment-bait −52% reach, question hooks the worst opening
# type, invented numbers the first thing a reader catches, hashtag walls read as spam.
_REFUSALS = [
    (re.compile(r"\b(comment(s|ing)? below|comment [\"'“]?\w+[\"'”]? (below|if)|dm me|inbox me|tag (a |some)?(one|friend|someone)|"
                r"repost if|share if|like if|agree\?|drop a \w+|type [\"'“]?\w+[\"'”]? below)", re.I),
     "Asks like \"comment below\", \"DM me\" or \"tag someone\" are left out: LinkedIn's 2026 feed cuts those posts' reach by about half."),
    (re.compile(r"\b(make up|invent|fabricate|add (a |some |any )?(fake |made[- ]up )?(stat|statistic|number|figure|percentage|data|study|survey|client|quote|testimonial)s?)\b", re.I),
     "A number, study, client or quote you haven't given Voyce stays out. Put the fact in the box and it will be used."),
    (re.compile(r"\b(start|open|begin|lead|first line|hook|opening)\w*\b.{0,40}\bquestion\b|\bquestion\b.{0,40}\b(as the |for the |to )?(start|open|hook|first line|opening)", re.I),
     "The first line stays a statement: question openings perform worst of every hook type in 2026 LinkedIn data."),
    (re.compile(r"\b(1\d|2\d|30|more|lots of|many|plenty of|a wall of)\b.{0,16}\bhashtags?\b|\bhashtags?\b.{0,24}\b(1\d|2\d|30|more|lots|wall|many)\b", re.I),
     "Hashtags stay at three or fewer: more than that reads as spam to the feed."),
]
_FILLER = set("the and for with that this then also just please make keep more less into from about only very end start "
              "ask tell say put add use them they people readers reader everyone should could would can post line lines".split())


def clean(text) -> str:
    return " ".join(str(text or "").split())[:MAX_CHARS]


def check(instruction) -> dict:
    """What an ask runs into. ok is False only when the whole ask is something Voyce won't do (nothing is left
    to carry out, so no rewrite is spent); otherwise the rewrite runs and notes say what was left out."""
    text = clean(instruction)
    notes = []
    rest = text
    for rx, why in _REFUSALS:
        if rx.search(rest):
            notes.append(why)
            rest = rx.sub(" ", rest)
    words = [w for w in re.findall(r"[a-z]{3,}", rest.lower()) if w not in _FILLER]
    return {"ok": not notes or len(words) >= 3, "notes": notes}


def prompt_block(instruction: str, role: str = "writer") -> str:
    """The ask as the writer (or the final editor) sees it, with the guardrails under it."""
    text = clean(instruction)
    if not text:
        return ""
    if role == "editor":
        return ("THE AUTHOR'S INSTRUCTION FOR THIS POST (it is why the post reads the way it does; keep what it "
                f"asked for unless it breaks a guardrail below):\n{text}\n\n{GUARDRAILS}")
    return ("THE AUTHOR'S INSTRUCTION FOR THIS POST (follow it for tone, angle, order, length and wording; where it "
            f"differs from the author's saved rules, this instruction wins for this one post):\n{text}\n\n{GUARDRAILS}")


# ── Repeated asks become rules ────────────────────────────────────────────────────────────────────────────
def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", clean(text).lower()) if w not in _FILLER}


def remember(company_id: str, user_id: str, instruction: str) -> int:
    """Keep the ask; return how many times something like it was asked for this profile in 60 days (this one
    included). Two or more and the page offers "Make it a rule"."""
    text = clean(instruction)
    if not text or not company_id:
        return 0
    now = datetime.utcnow()
    try:
        db.steer_log.insert_one({"company_id": company_id, "user_id": user_id, "text": text, "at": now})
        mine = _words(text)
        n = 0
        for row in db.steer_log.find({"company_id": company_id, "at": {"$gte": now - timedelta(days=60)}}, {"_id": 0, "text": 1}):
            w = _words(row.get("text", ""))
            if mine and w and len(mine & w) / len(mine | w) >= 0.5:
                n += 1
        return n
    except Exception:
        return 1


def rules_text(knowledge: str, instruction: str, cap: int = 4000) -> str:
    """The knowledge box with the ask added under one heading, once, within the writers' cap."""
    text = clean(instruction)
    base = (knowledge or "").rstrip()
    if not text:
        return base
    if text.lower() in base.lower():
        return base
    line = f"- {text}"
    if RULE_HEADING in base:
        new = base + "\n" + line
    else:
        new = (base + "\n\n" if base else "") + RULE_HEADING + "\n" + line
    if len(new) > cap:
        raise HTTPException(status_code=422, detail="Your writing rules box is full. Shorten it in the profile, then add this.")
    return new


def add_rule(company: dict, instruction: str) -> dict:
    from company import update_company
    new = rules_text(company.get("knowledge") or "", instruction)
    updated = update_company(company["id"], {"knowledge": new}) or {}
    return {"ok": True, "knowledge": updated.get("knowledge", new), "rule": clean(instruction)}


# ── Routes: the Create page's push-back, and "Make it a rule" when signed in ──────────────────────────────
class RewriteIn(BaseModel):
    post: str
    instruction: str = ""
    style: str = ""          # a lever ("hook", "story", "bolder", "warmer", "shorter") or "" for the instruction alone
    profile_id: str = ""


@router.post("/generate/rewrite")
def rewrite(body: RewriteIn, x_token: str = Header(None)):
    """The post on Create, rewritten as asked (or with a lever). Counts as a rewrite, never as a post."""
    import draft_styles
    from main import _require_user, _rate_limit, _daily_cap, _resolve_profile
    user = _require_user(x_token)
    _rate_limit(f"gen:{user['id']}", 20)
    post = (body.post or "").strip()
    if not post:
        raise HTTPException(status_code=400, detail="Write a post first, then tell Voyce what to change.")
    style = body.style or "custom"
    if style != "custom" and style not in draft_styles.STYLES:
        raise HTTPException(status_code=400, detail="Unknown style.")
    instruction = clean(body.instruction)
    if style == "custom" and not instruction:
        raise HTTPException(status_code=400, detail="Say what to change, e.g. \"less formal\" or \"lead with the client moment\".")
    verdict = check(instruction) if instruction else {"ok": True, "notes": []}
    if not verdict["ok"]:
        raise HTTPException(status_code=422, detail=" ".join(verdict["notes"]))
    _daily_cap(user, "rewrites")
    profile = _resolve_profile(user["id"], body.profile_id) or {}
    text = draft_styles.restyle_text(profile, post[:4000], style, instruction)
    times = remember(profile.get("id", ""), user["id"], instruction) if instruction else 0
    return {"post_text": text, "style": "Your ask" if style == "custom" else draft_styles.STYLES[style][0],
            "notes": verdict["notes"], "times_asked": times, "suggest_rule": bool(instruction) and times >= 2 and bool(profile.get("id"))}


class RuleIn(BaseModel):
    text: str


@router.post("/companies/{company_id}/rules")
def make_rule(company_id: str, body: RuleIn, x_token: str = Header(None)):
    """"Make it a rule": the ask goes into the profile's writing rules, which every writer reads."""
    from main import _require_user, _rate_limit
    user = _require_user(x_token)
    _rate_limit(f"steer-rule:{user['id']}", 20, 3600)
    company = db.companies.find_one({"id": company_id, "user_id": user["id"]}, {"_id": 0})
    if not company:
        raise HTTPException(status_code=404, detail="Not found")
    text = clean(body.text)
    if len(text) < 4:
        raise HTTPException(status_code=400, detail="That's too short to be a rule.")
    verdict = check(text)
    if not verdict["ok"]:
        raise HTTPException(status_code=422, detail=" ".join(verdict["notes"]))
    return add_rule(company, text)
