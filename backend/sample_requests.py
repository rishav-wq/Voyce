"""Free sample posts: the lead magnet on Rishav's LinkedIn profile (24 Nov 2026).

A consultant, coach or founder asks at /sample for three LinkedIn posts written from their own stories. The team
writes them by hand (the story/ pipeline, then a person checks every line) and sends them within two working days.
Research behind it: a done-for-you sample is the offer that fits a senior buyer, and a short form beats "message me
'sample'" because a DM gives no email, no consent and no screening (rishav-linkedin/profile-setup.md).

- Who it's for: consultants, coaches and founders. Anyone else (employees, job-seekers, students: most of
  Rishav's followers are engineers) is pointed to the free voice check, and nothing about them is kept.
- How many: SAMPLE_WEEKLY a week (5). A full week books the next one, with its real date, never a fake "spots left".
- Where it goes: each request lands in the Sales tracker as an interested lead (stage "spoke": "send the sample"),
  so the follow-up runs where the team already works. The requester gets one confirmation email; the team gets one
  email per request.
- Their data: what the form asks, kept for writing the samples and following up; a WhatsApp number only with its
  own tick (India's DPDP Act wants consent per purpose, freely given).
"""
import html
import logging
import os
import re
import secrets
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

import db
import ratelimit

router = APIRouter()
logger = logging.getLogger(__name__)

WEEKLY = int(os.getenv("SAMPLE_WEEKLY", "5"))
_WEEKS_AHEAD = 4                      # a request is booked into one of the next few weeks, or told we're full
IST = timezone(timedelta(hours=5, minutes=30))
FIT_ROLES = {"consultant": "Consultant", "coach": "Coach", "founder": "Founder or CXO"}
OTHER_ROLES = {"employee": "Employee", "jobseeker": "Job-seeker", "student": "Student"}

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[a-z]{2,24}$", re.I)
_LINKEDIN = re.compile(r"^(?:https?://)?(?:[a-z]{2,3}\.)?linkedin\.com/in/([^/?#\s]{2,100})/?(?:[?#].*)?$", re.I)


class SampleRequest(BaseModel):
    name: str = ""
    email: str = ""
    linkedin: str = ""
    role: str = ""
    whatsapp: str = ""
    whatsapp_ok: bool = False
    story: str = ""
    company: str = ""     # left empty by people; a bot that fills every field gets a polite "done" and nothing stored


def _today() -> date:
    return datetime.now(IST).date()


def _start_day(today: date) -> date:
    """The working day writing can start: today on a weekday, else Monday."""
    return today if today.weekday() < 5 else today + timedelta(days=7 - today.weekday())


def _plus_working_days(d: date, n: int) -> date:
    while n:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n -= 1
    return d


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _day(d: date) -> str:
    return f"{d.strftime('%A')} {d.day} {d.strftime('%b')}"


def plan(today: date | None = None) -> tuple[str, date] | None:
    """(week, deliver_by) for a new request: the first week from now with a slot left, delivered two working days
    after writing can start (Mon -> Wed; Thu -> Mon; weekend -> Wed). None when the next few weeks are all full."""
    start = _start_day(today or _today())
    for _ in range(_WEEKS_AHEAD):
        week = _monday(start).isoformat()
        if db.sample_requests.count_documents({"week": week, "status": "booked"}) < WEEKLY:
            return week, _plus_working_days(start, 2)
        start = _monday(start) + timedelta(days=7)
    return None


def _clean(req: SampleRequest) -> dict:
    """The form, checked: raises 400 with a line the page can show next to the button."""
    name = " ".join((req.name or "").split())[:80]
    email = (req.email or "").strip().lower()[:200]
    m = _LINKEDIN.match((req.linkedin or "").strip())
    role = (req.role or "").strip().lower()
    if not name:
        raise HTTPException(400, "Please add your name.")
    if not _EMAIL.match(email):
        raise HTTPException(400, "Please check your email address: the posts are sent there.")
    if role not in FIT_ROLES and role not in OTHER_ROLES:
        raise HTTPException(400, "Please choose what best describes you.")
    if role in FIT_ROLES and not m:
        raise HTTPException(400, "Please paste your LinkedIn profile link (it looks like linkedin.com/in/your-name). "
                                 "We write the posts from it.")
    phone = re.sub(r"[^\d+]", "", req.whatsapp or "")[:16] if req.whatsapp_ok else ""
    if phone and not 10 <= len(re.sub(r"\D", "", phone)) <= 15:
        raise HTTPException(400, "Please check your WhatsApp number, or leave it empty.")
    return {"name": name, "email": email, "role": role,
            "linkedin": f"https://www.linkedin.com/in/{m.group(1)}" if m else "",
            "whatsapp": phone, "whatsapp_ok": bool(phone),
            "story": (req.story or "").strip()[:800]}


@router.post("/sample")
def request_sample(req: SampleRequest, request: Request):
    if (req.company or "").strip():
        return {"status": "booked", "deliver_by": _day(_plus_working_days(_start_day(_today()), 2))}
    ip = ratelimit.client_ip(request)
    if not ratelimit.allow(f"sample:{ip}", 4, 3600) or not ratelimit.allow("sample:all", 80, 86400):
        raise HTTPException(429, "Too many requests from here just now. Please try again in an hour.")
    f = _clean(req)
    if f["role"] in OTHER_ROLES:
        # Not who the samples are for: counted (how many visitors fit), but nothing personal is kept.
        db.sample_requests.insert_one({"id": "s_" + secrets.token_urlsafe(8), "status": "voice_check",
                                       "role": f["role"], "created_at": datetime.now(timezone.utc).isoformat()})
        return {"status": "voice_check"}

    since = (datetime.now(timezone.utc) - timedelta(days=60)).isoformat()
    prev = db.sample_requests.find_one(
        {"status": "booked", "created_at": {"$gte": since}, "$or": [{"email": f["email"]}, {"linkedin": f["linkedin"]}]},
        {"_id": 0, "deliver_by": 1})
    if prev:
        return {"status": "already", "deliver_by": _day(date.fromisoformat(prev["deliver_by"]))}

    slot = plan()
    if not slot:
        raise HTTPException(409, "The next few weeks are fully booked. Try the free voice check below, "
                                 "or write to hello@voyce.co.in and we'll keep you in mind.")
    week, deliver = slot
    now = datetime.now(timezone.utc).isoformat()
    doc = {**f, "id": "s_" + secrets.token_urlsafe(8), "status": "booked", "week": week,
           "deliver_by": deliver.isoformat(), "created_at": now}
    db.sample_requests.insert_one(dict(doc))
    later = week != _monday(_start_day(_today())).isoformat()

    try:   # the tracker and the emails never cost the requester their booking
        import sales
        label = FIT_ROLES[f["role"]]
        note = f"Asked for free samples on the sample page. Deliver by {_day(deliver)}." + \
               (f" Their story: {f['story'][:180]}" if f["story"] else "")
        lead = sales.add_inbound({"name": f["name"], "email": f["email"], "linkedin": f["linkedin"],
                                  "phone": f["whatsapp"], "role": label, "source": "Sample page",
                                  "segment": f"Asked for LinkedIn help: {label}",
                                  "notes": ("Story for the samples: " + f["story"]) if f["story"] else ""},
                                 note, deliver.isoformat())
        db.sample_requests.update_one({"id": doc["id"]}, {"$set": {"lead_id": lead.get("id", "")}})
    except Exception:
        logger.exception("[sample] could not add the request to the Sales tracker")
    _email_requester(doc, deliver, later)
    _email_team(doc, deliver)
    return {"status": "booked", "deliver_by": _day(deliver), "next_week": later}


# ── Emails ────────────────────────────────────────────────────────────────────
def _email_requester(doc: dict, deliver: date, later: bool) -> bool:
    import notify
    first = doc["name"].split()[0]
    when = _day(deliver)
    paras = [f"Thanks, {html.escape(first)}. Your three posts will reach this inbox by <strong>{when}</strong>."
             + (" This week's samples were already taken, so yours is first in line for the next one." if later else "")]
    if not doc.get("story"):
        paras.append("One thing makes them much better: reply with one story you'd like a post about. A client "
                     "moment, a mistake, a decision you'd make differently. Two or three lines is plenty.")
    else:
        paras.append("Thanks for the story. We'll build one of the three around it.")
    paras.append("We write them from your LinkedIn profile and what you've shared, and a person checks every line "
                 "before it reaches you. Nothing is posted anywhere, and the posts are yours to keep.")
    footer = ("You're getting this because you asked for free sample posts at voyce.co.in/sample. We'll email you "
              "the samples and a few short follow-ups. Reply STOP and we won't email you again.")
    text = re.sub(r"<[^>]+>", "", "\n\n".join(paras)) + "\n\nRishav, Voyce\n\n" + footer
    body = notify._layout(f"Your sample posts: ready by {when}", paras + ["Rishav, Voyce"], footer=footer)
    return notify.send("", f"sample:{doc['id']}", "sample_confirm",
                       f"Your 3 sample posts: ready by {when}", text, body, to=doc["email"])


def _email_team(doc: dict, deliver: date) -> bool:
    import notify
    rows = [("Who", f"{doc['name']} ({FIT_ROLES[doc['role']]})"), ("LinkedIn", doc["linkedin"]),
            ("Email", doc["email"]), ("WhatsApp", doc["whatsapp"] or "not given"),
            ("Their story", doc["story"] or "none yet (the confirmation asks for one)"), ("Deliver by", _day(deliver))]
    paras = [f"{html.escape(k)}: {html.escape(v)}" for k, v in rows] + \
            ["It's in the Sales tracker as an interested lead. Reply to this email to write to them directly."]
    text = "\n".join(f"{k}: {v}" for k, v in rows) + "\n\nIt's in the Sales tracker as an interested lead."
    sent = False
    for admin in [e.strip() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]:
        sent |= notify.send("", f"sample_team:{doc['id']}:{admin}", "sample_team",
                            f"Sample request: {doc['name']}, by {_day(deliver)}", text,
                            notify._layout("New free-sample request", paras), to=admin, reply_to=doc["email"])
    return sent
