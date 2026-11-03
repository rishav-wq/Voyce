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
        "brand": "Solvai",                    # the product name clients see
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
        "clerk_publishable_env": "SOLVAI_CLERK_PUBLISHABLE_KEY",
        "clerk_secret_env": "SOLVAI_CLERK_SECRET_KEY",
        "sender_env": "SOLVAI_SENDER",
        "sender_default": "Solvai <solvai@notify.voyce.co.in>",   # Resend sends only from a verified domain (3 Nov: notify.voyce.co.in)
        "admins_env": "SOLVAI_ADMINS",
        "assets": "partners/solvai",          # under frontend/: landing, terms, privacy, logos
        "theme": {                            # Sam's own colour codes (solvai-landing_draft1.html)
            "navy": "#0A1739", "navy_deep": "#070F24", "orange": "#B9521C", "orange_hover": "#A04516",
            "coral": "#F88860", "blue": "#213C77", "ink": "#0A1739", "muted": "#5A6482",
            "line": "#E1E6F0", "sand": "#F4F1EA",
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


def sender(partner: dict | None) -> str:
    return os.getenv(partner["sender_env"], "") or partner["sender_default"] if partner else ""


def base_url(partner: dict | None) -> str:
    """Where a partner's links point: its first address, or the first test alias when it has no live one yet."""
    if not partner:
        return "https://voyce.co.in"
    return f"https://{partner['hosts'][0]}"


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
