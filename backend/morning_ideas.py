"""
morning_ideas.py — Today's ideas: up to three posts an author could write today, made before they
wake. Only what's true goes in (this week's news on their topics, their career, their rules), Claude
drafts, code checks every number against what its own source says, and a strict editor keeps only
the ideas that clear the bar. A thin day shows fewer ideas, never weak ones.

Runs as a background job (it takes a few minutes), so Claude gets time to think and there is no
fallback to a weaker model: a failed run is tried again later.
"""

import json
import logging
import os
import re
import time
from datetime import datetime, timedelta, timezone

import requests

import llm
from autonomous import news_search_args, profile_topics, topic_queries
from search import TAVILY_API_KEY, search_industry_news

logger = logging.getLogger(__name__)

# Measured 1 Nov 2026: drafting at medium effort cost ₹21-28 a profile, most of it thinking tokens
DRAFT_EFFORT = os.getenv("IDEAS_DRAFT_EFFORT", "low")
CANDIDATES = 8      # at 6, a strict editor left Sam two ideas and Harish one
BAR = 4.0           # the editor's average score an idea needs to be shown
_SCORES = ("specific", "timely", "theirs", "hook", "useful")


def _full_text(urls: list[str]) -> dict:
    """The articles' own text, so a number from the second half of a story isn't taken for an
    invention (the search only returns ~600 characters of each). {url: text}; {} on failure."""
    urls = [u for u in dict.fromkeys(urls) if u][:10]
    if not urls or not TAVILY_API_KEY:
        return {}
    try:
        r = requests.post("https://api.tavily.com/extract",
                          json={"api_key": TAVILY_API_KEY, "urls": urls, "extract_depth": "basic"}, timeout=40)
        r.raise_for_status()
        return {x.get("url"): (x.get("raw_content") or "")[:30000] for x in r.json().get("results") or []}
    except Exception:
        logger.exception("[Ideas] could not read the articles")
        return {}


def _norm(s: str) -> str:
    s = re.sub(r"(?<=\d),(?=\d)", "", (s or "").lower())   # 45,000 and 45000 are one number
    return re.sub(r"\s+", " ", re.sub(r"[^\w%.$₹ ]", " ", s)).strip()


# "your first 90 days", "in two years": spans of time, not claims a source has to back
_SPAN = r"(?!\s*(?:days?|weeks?|months?|years?|hours?|minutes?)\b)"


def _numbers(s: str) -> list[str]:
    # whole numbers only: without the guards, "90 days" skipped the 90 and caught a "9"
    return [n.replace(",", "") for n in re.findall(r"(?<![\d.,])\d+(?:[.,]\d+)*%?(?![\d.,%])" + _SPAN, s or "")]


def _problems(c: dict, source_text: str) -> list[str]:
    """What's wrong with a draft before the editor sees it: a number its source doesn't contain, or
    a proof that isn't in the source. (A long opening is the editor's to shorten, see make_ideas.)"""
    out, src = [], _norm(source_text)
    for num in set(_numbers(c.get("opening", "")) + _numbers(c.get("proof", ""))):
        if num.rstrip("%") not in src:
            out.append(f"the number {num} isn't in its source")
    words = [w for w in _norm(c.get("proof", "")).split() if len(w) > 3]
    if words and sum(w in src for w in words) / len(words) < 0.75:
        out.append("the proof isn't in its source")
    return out


def recent_made(company: dict, days: int = 30) -> list[str]:
    """What this profile made on Write lately (db.write_history), newest first."""
    import db
    since = datetime.now(timezone.utc) - timedelta(days=days)
    try:
        rows = db.write_history.find({"profile_id": company.get("id", ""), "created_at": {"$gte": since}},
                                     {"_id": 0, "text": 1}).sort("created_at", -1).limit(15)
        return [r["text"] for r in rows if (r.get("text") or "").strip()]
    except Exception:
        logger.exception("[Ideas] could not read what they made lately")
        return []


def make_ideas(company: dict, recent: list[str] | None = None) -> dict:
    """Today's ideas for one profile: {"ideas": [up to 3], "dropped": [...], "cost_usd", "secs"}.
    recent: posts they made lately (by default the Write page's history), so no idea repeats one."""
    t0, mark = time.time(), len(llm.usage_log)
    recent_made_list = recent_made(company) if recent is None else recent
    name, role = company.get("name", ""), company.get("designation", "")
    topics = profile_topics(company)
    nargs = news_search_args(company)

    # 1. What's true: this week's news on each topic, their career, their rules, what they posted
    news, seen = [], set()
    for t in topics:
        for n in search_industry_news(t, name, 3, queries=topic_queries(company, t), **nargs):
            if n.get("url") and n["url"] not in seen:
                seen.add(n["url"])
                news.append({**n, "topic": t})
    facts = (company.get("profile_facts") or "").strip()[:8000]
    rules = (company.get("knowledge") or "").strip()[:4000]
    posted = [str(x)[:300] for x in (company.get("linkedin_top_posts") or [])[:8]] + \
             [str(x)[:300] for x in recent_made_list[:15]]
    news_block = "\n".join(f"[N{i}] ({n['topic']}) {n['title']} — {n.get('published', '')[:16]}\n{n['snippet']}"
                           for i, n in enumerate(news, 1))

    # 2. Claude drafts
    draft = llm.claude_json(f"""You are the editor deciding what {name} ({role}) posts on LinkedIn today,
{datetime.now(timezone.utc).strftime('%d %B %Y')}. Their topics: {'; '.join(topics)}.

THEIR RULES (every opening line follows them; where they say nothing, name the reader in the first words):
{rules or '(none given)'}

THEIR CAREER (real facts):
{facts or '(none)'}

WHAT THEY POSTED OR MADE LATELY (no idea may repeat one of these topics or openings):
{chr(10).join('- ' + p.replace(chr(10), ' ') for p in posted) or '(nothing yet)'}

THIS WEEK'S NEWS ON THEIR TOPICS:
{news_block or '(none found)'}

Propose {CANDIDATES} candidate posts, each something only this person could write well, about one of
their topics, useful to the people they write for. Build most on a specific item from this week's news
(cite it as N<number>) and at least two on their own career. Spread them across their topics.

For each:
- topic: which of their topics
- opening: the post's actual first line, in their voice and rules, max 75 characters
- angle: one line on why this matters to their readers this week
- proof: the specific fact the post stands on, copied exactly from the news or career text
- source: "N<number>" for news, or "career"
- move: the one thing a reader can do after reading, in a short line
- post_type: insight | contrarian | lesson | how_to | story | data
Never invent a number, a quote, a client or an event. Skip a news item with no fact worth standing on.

Return ONLY JSON: {{"candidates":[{{"topic":"","opening":"","angle":"","proof":"","source":"","move":"","post_type":""}}]}}""",
                            effort=DRAFT_EFFORT, timeout=420)
    cands = [c for c in (draft.get("candidates") or []) if isinstance(c, dict)][:CANDIDATES + 2]

    # 3. Code checks each draft against its own source: the article's full text, or their career
    cited = {}
    for c in cands:
        m = re.fullmatch(r"N(\d+)", str(c.get("source", "")).strip())
        if m and 0 < int(m.group(1)) <= len(news):
            cited[id(c)] = news[int(m.group(1)) - 1]
    text = _full_text([n["url"] for n in cited.values()])
    passed, dropped = [], []
    for c in cands:
        n = cited.get(id(c))
        if n:
            c.update(source_title=n["title"], source_url=n["url"], published=n.get("published", "")[:16])
            source_text = " ".join((n["title"], n["snippet"], text.get(n["url"], "")))
        elif str(c.get("source", "")).strip() == "career":
            source_text = " ".join((facts, rules))
        else:
            dropped.append({**c, "why": "cites a source that doesn't exist"})
            continue
        problems = _problems(c, source_text)
        (dropped.append({**c, "why": "; ".join(problems)}) if problems else passed.append(c))

    # 4. A strict editor, with their whole rule book, scores what passed
    ideas = []
    if passed:
        judge = llm.claude_json(f"""You are a strict LinkedIn editor for {name} ({role}). Score each candidate 1-5 on:
- specific: stands on a concrete fact, not a generality anyone could write
- timely: tied to this week, or a career story that never dates
- theirs: only this author, with their topics and career, could write it
- hook: the opening line would stop their readers scrolling
- useful: the reader gets a move they can act on
Set keep=false for anything generic, any recycled LinkedIn cliche, any opening that overpromises, and
anything that breaks their rules. Judge the idea, not the finished post: naming the source and its date is
done when the post is written, so a missing citation is never a reason to drop or mark down an idea.
A true story from their own career never dates: it scores 4 or 5 on timely.
If a kept candidate's hook is under 4, or its opening is over 75 characters ("too_long"), write
"sharper_opening": a better
first line, max 75 characters, following their rules, built only on the same facts.
Then choose up to 3 on DIFFERENT topics, best first.

THEIR RULES:
{rules or '(none given)'}

CANDIDATES:
{json.dumps([{k: c.get(k) for k in ('topic', 'opening', 'angle', 'proof', 'move', 'post_type')}
             | {'id': i, 'too_long': len(c.get('opening', '')) > 75} for i, c in enumerate(passed)], ensure_ascii=False, indent=1)}

Return ONLY JSON: {{"scores":[{{"id":0,"specific":0,"timely":0,"theirs":0,"hook":0,"useful":0,"keep":true,"sharper_opening":"","note":""}}],"best":[0,1,2]}}""",
                                model=llm.CLAUDE_CHECK_MODEL, effort="medium", timeout=300)
        for s in judge.get("scores") or []:
            if isinstance(s, dict) and isinstance(s.get("id"), int) and 0 <= s["id"] < len(passed):
                passed[s["id"]]["scores"] = s
        for i in judge.get("best") or []:
            if not (isinstance(i, int) and 0 <= i < len(passed)):
                continue
            c, s = passed[i], passed[i].get("scores") or {}
            better = (s.get("sharper_opening") or "").strip()
            if better and len(better) <= 75 and not _problems({**c, "opening": better}, " ".join(
                    (c.get("source_title", ""), c.get("proof", ""), facts, text.get(c.get("source_url", ""), "")))):
                c["opening_was"], c["opening"] = c["opening"], better
                s["hook"] = max(s.get("hook", 0), 4)
            c["score"] = round(sum(s.get(k, 0) for k in _SCORES) / len(_SCORES), 1)
            fits = len(c.get("opening", "")) <= 75   # a long opening the editor didn't shorten can't be shown
            if s.get("keep") is not False and fits and c["score"] >= BAR and s.get("specific", 0) >= 4:
                ideas.append(c)
        kept = {id(c) for c in ideas}
        dropped += [{**c, "why": (c.get("scores") or {}).get("note") or "below the bar"} for c in passed if id(c) not in kept]

    price = {"claude-opus-5-5": (4, 20), "claude-sonnet-5-5": (2, 10)}
    cost = sum(i * price.get(m, (4, 20))[0] / 1e6 + o * price.get(m, (4, 20))[1] / 1e6
               for m, i, o in llm.usage_log[mark:])
    return {"ideas": ideas[:3], "dropped": dropped, "news_found": len(news), "made_at": datetime.now(timezone.utc).isoformat(),
            "cost_usd": round(cost, 3), "secs": round(time.time() - t0)}


# ── Today's ideas: made by 5-7 AM in each profile's own time zone, kept for the day ──────────────
ACTIVE_DAYS = 3        # made only for profiles opened on Write lately; anyone else gets them on their first visit
PER_USER = 3           # at most this many of one person's profiles a morning (the team keeps up to 200 sample profiles)
FREE_TRIAL_DAYS = 7    # a free account gets them for its first week
STUCK_MINUTES = 15     # a "making" older than this was cut off (a deploy mid-make) and can start again


def local_today(company: dict) -> str:
    from zoneinfo import ZoneInfo
    from company import DEFAULT_TZ, valid_tz
    try:
        return datetime.now(ZoneInfo(valid_tz(company.get("timezone", "")) or DEFAULT_TZ)).strftime("%Y-%m-%d")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def eligible(user: dict) -> bool:
    """Passes and the team always; a free account in its first week."""
    import auth as auth_module
    plan = auth_module.get_gen_info(user["id"]).get("plan")
    if plan in ("pro", "admin"):
        return True
    try:
        return datetime.fromisoformat(str(user.get("created_at", ""))[:19]) > datetime.now() - timedelta(days=FREE_TRIAL_DAYS)
    except ValueError:
        return False


def stored(company_id: str, day: str) -> dict | None:
    import db
    return db.daily_ideas.find_one({"_id": f"{company_id}:{day}"})


def note_opened(company: dict) -> None:
    """The profile was opened on Write: the morning job makes its ideas for the next few days.
    Written at most hourly."""
    import db
    now = datetime.now(timezone.utc)
    seen = company.get("ideas_seen_at")
    if seen and seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    if not seen or now - seen > timedelta(hours=1):
        db.companies.update_one({"id": company.get("id", "")}, {"$set": {"ideas_seen_at": now}})


def _shown(c: dict) -> dict:
    """An idea as the page shows it, with the brief Write uses when it's chosen."""
    src = (f"{c.get('source_title', '')} ({c.get('published', '')[:16]}) {c.get('source_url', '')}".strip()
           if c.get("source_url") else "their own career")
    brief = (f"Write the post around this idea.\nOpening line: {c.get('opening', '')}\nAngle: {c.get('angle', '')}\n"
             f"The fact it stands on: {c.get('proof', '')}\nSource: {src}\nThe reader's move: {c.get('move', '')}")
    return {k: c.get(k, "") for k in ("topic", "opening", "angle", "proof", "move", "post_type", "source_title",
                                       "source_url", "published")} | {"score": c.get("score"), "brief": brief}


def make_and_store(company: dict) -> None:
    """Make one profile's ideas for its today and keep them. A claim on the day's record means two
    workers (the morning sweep and a first visit) never make them twice."""
    import db
    from pymongo.errors import DuplicateKeyError
    day = local_today(company)
    key = f"{company.get('id', '')}:{day}"
    now = datetime.now(timezone.utc)
    try:
        db.daily_ideas.insert_one({"_id": key, "profile_id": company.get("id", ""), "user_id": company.get("user_id", ""),
                                   "day": day, "status": "making", "created_at": now})
    except DuplicateKeyError:
        return
    try:
        out = make_ideas(company)
        db.daily_ideas.update_one({"_id": key}, {"$set": {
            "status": "ready", "ideas": [_shown(c) for c in out["ideas"]], "made_at": out["made_at"],
            "cost_usd": out["cost_usd"], "secs": out["secs"], "news_found": out["news_found"]}})
        logger.info(f"[Ideas] {company.get('id')}: {len(out['ideas'])} ideas, ${out['cost_usd']}, {out['secs']}s")
    except Exception as e:
        logger.exception(f"[Ideas] {company.get('id')} failed")
        db.daily_ideas.update_one({"_id": key}, {"$set": {"status": "failed", "error": f"{type(e).__name__}: {e}"[:300],
                                                          "failed_at": now}})


def retry_if_failed(company: dict) -> bool:
    """A failed day can be tried again after half an hour (Claude was down, say), and a "making"
    cut off by a restart after STUCK_MINUTES. True if cleared."""
    import db
    doc = stored(company.get("id", ""), local_today(company))
    if not doc:
        return False
    status = doc.get("status")
    when, wait = {"failed": (doc.get("failed_at"), timedelta(minutes=30)),
                  "making": (doc.get("created_at"), timedelta(minutes=STUCK_MINUTES))}.get(status, (None, None))
    if when:
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        if datetime.now(timezone.utc) - when > wait:
            db.daily_ideas.delete_one({"_id": doc["_id"], "status": status})
            return True
    return False


def morning_sweep() -> None:
    """Hourly: the profiles opened on Write in the last few days (each person's latest PER_USER) whose
    local time is 5-7 AM and whose ideas for today aren't made yet. One at a time: each takes about a minute."""
    import db
    from zoneinfo import ZoneInfo
    from company import DEFAULT_TZ, valid_tz
    since = datetime.now(timezone.utc) - timedelta(days=ACTIVE_DAYS)
    users = {u["id"]: u for u in db.users.find({"last_seen": {"$gte": since}}, {"_id": 0})}
    per_user: dict = {}
    for company in db.companies.find({"user_id": {"$in": list(users)}, "ideas_seen_at": {"$gte": since}},
                                     {"_id": 0}).sort("ideas_seen_at", -1):
        per_user[company["user_id"]] = per_user.get(company["user_id"], 0) + 1
        if per_user[company["user_id"]] > PER_USER:
            continue
        try:
            hour = datetime.now(ZoneInfo(valid_tz(company.get("timezone", "")) or DEFAULT_TZ)).hour
            if not 5 <= hour < 7 or not eligible(users[company["user_id"]]):
                continue
            retry_if_failed(company)
            if not stored(company.get("id", ""), local_today(company)):
                make_and_store(company)
        except Exception:
            logger.exception(f"[Ideas] sweep skipped {company.get('id')}")
