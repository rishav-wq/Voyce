"""Partners: firms that offer Voyce to their own clients under their own brand (Nov 2026: CUSP's Solvai).

A partner has its own address, its own sign-in (a separate Clerk application, so its clients' accounts are
never Voyce accounts), its own look, and seats instead of passes. Everything here switches on only for a
request made on a partner's address; voyce.co.in never sees any of it.

Each request's partner is set by the middleware in main.py (current()). Background jobs read the partner
from the account or profile they work on (for_user, for_company).

Keys and anything secret come from the environment, never from this file:
  SOLVAI_CLERK_PUBLISHABLE_KEY, SOLVAI_CLERK_SECRET_KEY   the Solvai Clerk application
  PARTNER_HOST_ALIASES        extra addresses for testing, "preview.example.com=cusp,other=cusp"
  SOLVAI_ADMINS               more CUSP team emails, comma-separated
  SOLVAI_SENDER               the From of Solvai's emails (until cusp.services is verified with Resend)
"""
import contextvars
import os
from datetime import datetime, timezone

# CUSP's 4 I's: added to every Solvai client's writing rules, after the client's own (decided with Rishav,
# 2 Nov 2026: the rules from day one, the "checked against the 4 I's" card later).
_CUSP_HOUSE_RULES = """CUSP's house rules (the 4 I's), for every post:
1. Intriguing: the first line is 75 characters at most and makes the reader need the next one.
2. Informative: real facts only. Name the source and year of every number. Never invent a statistic, client, result or quote.
3. Insightful: one non-obvious take the reader hasn't heard, not a summary of the news.
4. Implementable: end each point with one concrete move the reader can make this week."""

PARTNERS: dict = {
    "cusp": {
        "id": "cusp",
        "brand": "SOLVAI",                    # the product name clients see, in Sam's own spelling (his page, the logo)
        "brand_full": "SOLVAI by CUSP",
        "tagline": "Originality, Amplified.",
        "company": "Acies Cusp Services LLP",
        "company_short": "CUSP",
        "site": "https://www.cusp.services",
        "hosts": ["solvai.cusp.services"],
        "support_email": "sam@cusp.services",   # replies to Solvai's emails (Sam, 3 Nov 2026: "sam@ only, for the moment")
        "waitlist_to": "sam@cusp.services",     # where the landing page's waiting-list requests go
        "admins": ["sam@cusp.services"],     # CUSP team: the client panel at /partner
        "max_admins": 1,                      # Sam, 3 Nov 2026: only he opens the panel for now
        "seats": 10,                          # seats on CUSP's plan (billed even when unused)
        "min_seats": 5,
        "rate_inr": 1400,                     # per seat per month
        "setup_inr": 1000,                    # once, on the first bill
        "drafts_per_seat": 60,                # a month: posts, carousels, cards, previews, idea lists
        "ai_pictures_per_seat": 10,           # a month, inside the 60
        "profiles_per_seat": 1,               # each client posts as themselves
        "invite_only": True,
        "auto_posting": False,                # LinkedIn API terms 3.1(26): a post goes live from a person's click
        "scheduling": False,
        "house_rules": _CUSP_HOUSE_RULES,
        # Voyce's examples are for coaches and consultants; CUSP's clients lead IT, cloud and security firms
        # (branding.transform, on the files as written: "Voyce" becomes the brand afterwards)
        "copy": [
            ("Answer one question and Voyce writes your first post, from this week's news in your niche. "
             "Nothing posts until you say so.",
             "Tell Voyce what you write about and it drafts your first post now, from this week's news in your field. "
             "CUSP will refine your voice with you. Nothing is posted until you tap Approve."),
            ("e.g. leadership, B2B marketing, fundraising", "e.g. cloud cost, AWS partnerships, security for mid-size firms"),
            ("e.g. Fractional CMO, Leadership Coach, Consultant", "e.g. Founder & CEO, CISO, Head of Cloud"),
            ("<span>Queue</span>", "<span>Today</span>"),   # the landing's word for the morning draft
            ("e.g. why most sales follow-ups die at the second email…",
             "e.g. why most cloud migrations stall in the second quarter…"),
            ('<p class="auth-tagline">LinkedIn posts that sound like <em class="you">you</em>.</p>',
             '<p class="auth-tagline">Originality, Amplified.</p>'),
        ],
        "clerk_publishable_env": "SOLVAI_CLERK_PUBLISHABLE_KEY",
        "clerk_secret_env": "SOLVAI_CLERK_SECRET_KEY",
        "sender_env": "SOLVAI_SENDER",
        "sender_default": "SOLVAI <solvai@notify.voyce.co.in>",   # Resend sends only from a verified domain (3 Nov: notify.voyce.co.in)
        "admins_env": "SOLVAI_ADMINS",
        "assets": "partners/solvai",          # under frontend/: landing, terms, privacy, logos, app.css
        "fonts": ["https://fonts.googleapis.com/css2?family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700"
                  "&display=swap"],           # the landing's type, in the app too (app.css)
        "theme": {                            # Sam's own colour codes (solvai-landing_draft1.html)
            "navy": "#0A1739", "navy_deep": "#070F24", "orange": "#B9521C", "orange_hover": "#A04516",
            "coral": "#F88860", "blue": "#213C77", "ink": "#0A1739", "muted": "#5A6482",
            "line": "#E1E6F0", "sand": "#F4F1EA",
        },
        # Voyce's other colours in SOLVAI's palette (launch audit, ui-report §7): light greys darkened to readable
        # text, amber "needs you" to orange tints, greens to one success green, blues to orange and ink
        "recolour": {
            "#98a0ae": "#5A6482", "#a7aebb": "#5A6482", "#8a909c": "#5A6482", "#7c8698": "#5A6482",
            "#7a8190": "#5A6482", "#6d7482": "#5A6482", "#aaa": "#5A6482", "#999": "#5A6482", "#888": "#5A6482",
            "#16181d": "#0A1739", "#1a1a1a": "#0A1739", "#e4e7ec": "#E1E6F0", "#eef0f4": "#E1E6F0",
            "#f6f7f9": "#F4F6FA", "#f5f5f7": "#F4F6FA", "#e7ebf4": "#EEF1F8",
            "#b07d1a": "#B9521C", "#d18b00": "#B9521C", "#9a5b00": "#A04516",
            "#fff8e6": "#FDF1EA", "#fffdf6": "#FDF1EA", "#fdf3e1": "#FDF1EA", "#f0d58a": "#F3CDB5", "#ecdca6": "#F3CDB5",
            "#057642": "#1E7A4F", "#18a07a": "#1E7A4F", "#e6f4ea": "#E7F4EC", "#a5d6a7": "#B7DDC6", "#8fe3bd": "#6FD3A0",
            "#c0392b": "#B42318",
            "#8fb4ff": "#FFFFFF", "#7aa2ff": "#E06D2E", "#5c8fff": "#E06D2E", "#2f5bc4": "#0A1739", "#3b66c4": "#0A1739",
        },
    },
}

USD_INR = float(os.getenv("USD_INR", "90"))   # for costs shown in rupees (Nov 2026)

_current: contextvars.ContextVar = contextvars.ContextVar("partner", default=None)


def _aliases() -> dict:
    out = {}
    for pair in (os.getenv("PARTNER_HOST_ALIASES") or "").split(","):
        host, _, pid = pair.strip().partition("=")
        if host and pid.strip() in PARTNERS:
            out[host.strip().lower()] = pid.strip()
    return out


def for_host(host: str) -> dict | None:
    host = (host or "").split(":")[0].strip().lower()
    if not host:
        return None
    for p in PARTNERS.values():
        if host in p["hosts"]:
            return p
    pid = _aliases().get(host)
    return PARTNERS.get(pid) if pid else None


def get(partner_id: str | None) -> dict | None:
    return PARTNERS.get(partner_id or "")


def for_user(user: dict | None) -> dict | None:
    return get((user or {}).get("partner_id"))


def for_company(company: dict | None) -> dict | None:
    return get((company or {}).get("partner_id"))


def current() -> dict | None:
    """The partner whose address this request came in on, or None on voyce.co.in."""
    return _current.get()


def set_current(partner: dict | None):
    return _current.set(partner)


def reset_current(token) -> None:
    _current.reset(token)


def clerk_keys(partner: dict | None) -> tuple[str, str]:
    """(publishable key, secret key) of the sign-in a partner's clients use; ("", "") until it is set up."""
    if not partner:
        return "", ""
    return os.getenv(partner["clerk_publishable_env"], ""), os.getenv(partner["clerk_secret_env"], "")


def admins(partner: dict | None) -> set:
    if not partner:
        return set()
    extra = [e.strip().lower() for e in (os.getenv(partner["admins_env"]) or "").split(",") if e.strip()]
    return {e.lower() for e in partner["admins"]} | set(extra)


def is_admin(user: dict | None, partner: dict | None = None) -> bool:
    """A partner's team member: signed in on that partner's sign-in, with an email the partner named."""
    partner = partner or for_user(user)
    return bool(partner and user and user.get("partner_id") == partner["id"]
                and (user.get("email") or "").lower() in admins(partner))


def seat_missing(user: dict | None) -> dict | None:
    """The partner of an account that may not use the app: a partner account with no active seat (never
    invited, or removed) that isn't one of the partner's team. None for everyone else."""
    partner = for_user(user)
    if partner and (user or {}).get("seat") != "active" and not is_admin(user, partner):
        return partner
    return None


def seat_needed_detail(partner: dict) -> str:
    return (f"SEAT_NEEDED:Your account doesn't have a {partner['brand']} seat. "
            f"Ask {partner['company_short']} to invite you.")


def sender(partner: dict | None) -> str:
    return os.getenv(partner["sender_env"], "") or partner["sender_default"] if partner else ""


def base_url(partner: dict | None) -> str:
    """Where a partner's links point: its first address, or the first test alias when it has no live one yet."""
    if not partner:
        return "https://voyce.co.in"
    return f"https://{partner['hosts'][0]}"


def url_for_request(partner: dict, host_header: str) -> str:
    """The partner's address a link should use for this request: the Host it came in on when that is exactly
    one of the partner's addresses (a test alias included), else its main one. Never the raw header: a Host
    like "solvai.cusp.services:x@evil.example" still selects the partner but parses as evil.example in a URL."""
    host = (host_header or "").strip().lower()
    if host in partner["hosts"] or _aliases().get(host) == partner["id"]:
        return f"https://{host}"
    return base_url(partner)


def month_key(now: datetime | None = None) -> str:
    """Seats refill on the first of each month (UTC)."""
    return (now or datetime.now(timezone.utc)).strftime("%Y-%m")


def next_month_start(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return datetime(now.year + (now.month == 12), now.month % 12 + 1, 1, tzinfo=timezone.utc)


def house_rules(company: dict | None) -> str:
    p = for_company(company)
    return (p or {}).get("house_rules", "") or ""


def with_house_rules(rules: str, company: dict | None) -> str:
    """A client's own rules, then their partner's house rules (theirs win where the two differ)."""
    extra = house_rules(company)
    if not extra:
        return rules
    return (rules.rstrip() + "\n\n" if rules.strip() else "") + extra + \
        ("\nWhere the author's own rules above say otherwise, theirs win." if rules.strip() else "")
