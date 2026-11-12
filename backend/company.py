import re
import uuid
from datetime import datetime
from zoneinfo import ZoneInfo
from scraper import scrape_company

import db

_P = {"_id": 0}

# Post times are wall-clock times in the profile's own time zone. Profiles made
# before time zones existed were scheduled in India time, so that stays the
# fallback rather than silently moving anyone's posts.
DEFAULT_TZ = "Asia/Kolkata"


# Old names some browsers still report (Chrome says "Asia/Calcutta"): store the
# current one, so the same zone never looks like two.
_TZ_ALIASES = {"Asia/Calcutta": "Asia/Kolkata", "Asia/Saigon": "Asia/Ho_Chi_Minh",
               "Asia/Katmandu": "Asia/Kathmandu", "Asia/Rangoon": "Asia/Yangon",
               "Europe/Kiev": "Europe/Kyiv", "America/Buenos_Aires": "America/Argentina/Buenos_Aires"}


def valid_tz(tz: str) -> str:
    """The IANA name if it's real (e.g. "America/New_York"), else ""."""
    tz = (tz or "").strip()
    tz = _TZ_ALIASES.get(tz, tz)
    if not tz:
        return ""
    try:
        ZoneInfo(tz)
        return tz
    except Exception:
        return ""


def save_company(profile: dict) -> dict:
    website_url = profile.get("website_url", "").strip()

    if website_url:
        company_id = website_url.replace("https://", "").replace("http://", "").split("/")[0]
    else:
        company_id = re.sub(r"[^a-z0-9]+", "-", profile["name"].lower()).strip("-")
    # The id is written into the dashboard's onclick handlers, so it may only hold
    # characters that are inert there — a website typed as  x.com');alert(1)//
    # used to become an id that ran as code in the page.
    company_id = re.sub(r"[^a-z0-9.\-]+", "-", company_id.lower()).strip("-.") or uuid.uuid4().hex[:10]

    # The derived id is only stable for the SAME user re-creating the SAME
    # profile. Any other occupant of the slot — another user's profile with the
    # same name/domain, or this user's differently-named one — must get a fresh
    # id, or replace_one(upsert=True) below silently reassigns their profile.
    existing = db.companies.find_one({"id": company_id}, _P)
    if existing and (existing.get("user_id") != profile.get("user_id", "")
                     or existing.get("name") != profile["name"]):
        company_id = f"{company_id}-{uuid.uuid4().hex[:6]}"

    scrape_result = {}
    if website_url:
        try:
            scrape_result = scrape_company(website_url, profile["name"])
        except Exception:
            scrape_result = {}

    entry = {
        "id":               company_id,
        "user_id":          profile.get("user_id", ""),
        "profile_type":     profile.get("profile_type", "company"),
        "website_type":     profile.get("website_type", "own"),
        "name":             profile["name"],
        "website_url":      website_url,
        "linkedin_url":     profile.get("linkedin_url", ""),
        "li_photo":         profile.get("li_photo", ""),      # base64 JPEG from "Fill from LinkedIn"
        "industry":         profile["industry"],
        "tone":             profile.get("tone", "professional"),
        "post_time":        profile["post_time"],
        "timezone":         valid_tz(profile.get("timezone", "")) or DEFAULT_TZ,
        # LinkedIn's API terms bar automated posting, and the buyer is putting
        # their own name on every post: new profiles ask before publishing.
        # Posting without review is an explicit, recorded opt-in.
        "approval_mode":    profile.get("approval_mode", True),
        "website_content":"\n\n".join(scrape_result.get("raw_pages", {}).values())[:4000],
        "analysis":         scrape_result.get("analysis", {}),
        "brand_color":      scrape_result.get("brand_color", ""),
        "pages_scraped":    scrape_result.get("pages_scraped", 0),
        "blog_posts_found": scrape_result.get("blog_posts_found", 0),
        "linkedin_analysis": {},
        "linkedin_top_posts": [],
        "created_at":       datetime.now().isoformat(),
        "active":           profile.get("active", True),
        "carousel_enabled": profile.get("carousel_enabled", False),
        "carousel_theme":   profile.get("carousel_theme", ""),
        "designation":      profile.get("designation", ""),
        "allowed_hooks":    profile.get("allowed_hooks", []),
        "tone_shift":       profile.get("tone_shift", False),
        "knowledge":        profile.get("knowledge", ""),   # curated facts/rules the AI must use
        "product_name":     profile.get("product_name", ""),  # optional subject label (e.g. "Voyce")
    }

    db.companies.replace_one({"id": company_id}, {"_id": company_id, **entry}, upsert=True)
    return entry


def get_company(company_id: str) -> dict | None:
    return db.companies.find_one({"id": company_id}, _P)


def list_companies(user_id: str = "") -> list[dict]:
    q = {"user_id": user_id} if user_id else {}
    return list(db.companies.find(q, _P))


def delete_company(company_id: str):
    db.companies.delete_one({"id": company_id})


def update_company(company_id: str, data: dict) -> dict | None:
    c = db.companies.find_one({"id": company_id}, _P)
    if not c:
        return None

    # carousel_enabled/carousel_theme are managed solely by the card's PATCH endpoint,
    # so a form edit can never silently reset them.
    for field in ("name", "industry", "tone", "post_time", "linkedin_url",
                  "website_type", "designation", "allowed_hooks", "tone_shift", "knowledge",
                  "product_name", "li_photo"):
        if field in data:
            c[field] = data[field]
    # An edit form without a time zone (or with a bad one) keeps the stored one.
    tz = valid_tz(data.get("timezone", ""))
    if tz:
        c["timezone"] = tz

    # An edit that doesn't send the website at all (a partner team's setup edit) leaves it alone; only an
    # empty website_url clears it. It used to read a missing one as cleared and wipe what the site said.
    new_url = (data.get("website_url") or "").strip() if "website_url" in data else c.get("website_url", "")
    old_url = c.get("website_url", "")
    if not new_url and old_url:
        c["website_url"]     = ""
        c["website_content"] = ""
        c["analysis"]        = {}
        c["pages_scraped"]   = 0
    elif new_url and new_url != old_url:
        try:
            sr = scrape_company(new_url, c["name"])
            c["website_url"]     = new_url
            c["website_content"] = "\n\n".join(sr.get("raw_pages", {}).values())[:4000]
            c["analysis"]        = sr.get("analysis", {})
            c["brand_color"]     = sr.get("brand_color", "")
            c["pages_scraped"]   = sr.get("pages_scraped", 0)
        except Exception:
            c["website_url"] = new_url
    elif new_url:
        c["website_url"] = new_url

    db.companies.replace_one({"id": company_id}, {"_id": company_id, **c})
    return c


def toggle_company(company_id: str, active: bool):
    db.companies.update_one({"id": company_id}, {"$set": {"active": active}})


def set_scheduled_type(company_id: str, day: str, post_type: str) -> bool:
    """Set (or clear) a per-day post-type override for the calendar. An empty
    post_type clears that day back to the automatic rotation. Stored as
    scheduled_types: {date: type}."""
    c = db.companies.find_one({"id": company_id}, _P)
    if not c:
        return False
    sched = c.get("scheduled_types", {}) or {}
    if post_type:
        sched[day] = post_type
    else:
        sched.pop(day, None)
    db.companies.update_one({"id": company_id}, {"$set": {"scheduled_types": sched}})
    return True


def save_linkedin_data(company_id: str, linkedin_result: dict):
    if linkedin_result.get("type") == "pdf":
        # Career facts only. It used to overwrite the voice too, wiping any pasted posts.
        result = db.companies.update_one({"id": company_id}, {"$set": {
            "profile_facts": linkedin_result.get("profile_text", ""),
            "profile_facts_at": datetime.now().isoformat()}})
        return result.matched_count > 0
    result = db.companies.update_one(
        {"id": company_id},
        {"$set": {
            "linkedin_analysis":  linkedin_result.get("analysis", {}),
            "linkedin_top_posts": linkedin_result.get("top_posts", []),
            "linkedin_data_type": linkedin_result.get("type", ""),
        }}
    )
    add_length_rule(company_id, linkedin_result.get("top_posts") or [])
    return result.matched_count > 0


# The person's usual post length, written into their rules when Voyce first reads their posts (12 Nov 2026).
# Every sample pack needed it: a loose length let 4 of 5 posts run about 30% long. The writers already keep
# to a "120-180 words" range in the rules box (generator.word_cap), so this is a line there they can see and
# change. Never added over a length they already wrote, nor past the box's 4,000 characters.
_RANGE = re.compile(r"\d{2,4}\s*(?:-|–|—|to)\s*\d{2,4}\s*words", re.I)


def _post_text(p) -> str:
    return p if isinstance(p, str) else str((p or {}).get("text") or (p or {}).get("commentary") or "")


def length_range(posts: list) -> tuple[int, int] | None:
    """(low, high) in words from their own posts, rounded to tens: at least 80 (a post from the news needs
    room) and at least 40 words wide. None without a real post to measure."""
    counts = [len([w for w in _post_text(p).split() if not w.startswith("#")]) for p in posts]
    counts = [n for n in counts if n >= 30]
    if not counts:
        return None
    low = max(80, min(counts) // 10 * 10)
    high = min(400, max(low + 40, -(-max(counts) // 10) * 10))
    return low, high


def add_length_rule(company_id: str, posts: list) -> str:
    """Add "Length: 110–180 words, like my own posts." to the profile's rules; returns the line added, or ""."""
    try:
        rng = length_range(posts)
        if not rng:
            return ""
        c = db.companies.find_one({"id": company_id}, {"_id": 0, "knowledge": 1}) or {}
        rules = (c.get("knowledge") or "").rstrip()
        if _RANGE.search(rules):
            return ""
        line = f"Length: {rng[0]}–{rng[1]} words, like my own posts."
        new = (rules + "\n" + line) if rules else line
        if len(new) > 4000:
            return ""
        db.companies.update_one({"id": company_id}, {"$set": {"knowledge": new}})
        return line
    except Exception:
        return ""   # a missing length line never stops the posts being saved
