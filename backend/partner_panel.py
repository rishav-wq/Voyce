"""A partner's own panel (CUSP's team at /partner on its address) and Voyce's view of its partners.

The partner's team sees its clients, invites people to seats, reads their drafts and posts, edits their
setup and reminds them. It can never approve, edit a draft or publish for a client: LinkedIn's User Agreement
(2.2, 8.2) forbids sharing an account, and the risk would fall on Voyce's one LinkedIn app, so on every
Voyce user. Each client taps Approve themselves.

Every query here is scoped to the partner of the request's address and to accounts tagged with it. Shown
about a client: what they typed into their setup, never data pulled from LinkedIn beyond connected yes/no
(LinkedIn API terms 3.1(2), 3.1(9)).
"""
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from pymongo import ReturnDocument

import auth as auth_module
import db
import linkedin as li
import notify
import partners
import ratelimit
from company import list_companies, update_company

router = APIRouter()
logger = logging.getLogger(__name__)
INVITE_DAYS = 14
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _sender_name(user: dict, partner: dict) -> str:
    """How the team member is named in an invite or a reminder: "MB Sam", or the firm when the sign-in gave no
    real name (Clerk with email codes leaves only the address's first part, which made "sam invited you")."""
    name = (user.get("name") or "").strip()
    handle = (user.get("email") or "").split("@")[0].strip().lower()
    return partner["company_short"] if not name or name.lower() == handle else name


def _waiting(uid: str) -> dict:
    """A client's drafts still waiting for Approve. A draft expires after 48 hours, but it's only marked so when the
    client opens their queue, and the clients Sam needs to nudge are the ones who don't (4 Nov 2026 launch audit)."""
    from autonomous import PENDING_TTL_HOURS
    cutoff = (datetime.now() - timedelta(hours=PENDING_TTL_HOURS)).isoformat()
    return {"user_id": uid, "status": "pending", "created_at": {"$gte": cutoff}}


# ── who's asking ──────────────────────────────────────────────────────────────

def _user(x_token: str) -> dict:
    user = auth_module.get_user_by_token(x_token or "")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def _team(x_token: str) -> tuple[dict, dict]:
    """The partner's team member asking, on the partner's own address."""
    partner = partners.current()
    if not partner:
        raise HTTPException(status_code=404, detail="Not found")
    user = _user(x_token)
    if not partners.is_admin(user, partner):
        raise HTTPException(status_code=403, detail=f"Only {partner['company_short']}'s team can open this.")
    return user, partner


def _client(partner: dict, uid: str) -> dict:
    u = db.users.find_one({"id": uid, "partner_id": partner["id"]}, {"_id": 0})
    if not u:
        raise HTTPException(status_code=404, detail="Client not found")
    return u


def _voyce_admin(x_token: str) -> dict:
    if partners.current():
        raise HTTPException(status_code=404, detail="Not found")
    user = _user(x_token)
    if auth_module.get_gen_info(user["id"]).get("plan") != "admin":
        raise HTTPException(status_code=403, detail="Voyce team only")
    return user


# ── activity: what the team's feed and counts read ────────────────────────────

def log(user: dict | None, kind: str, detail: str = "", by: str = "", partner_id: str = ""):
    """One event for a partner's feed: posted, approved, skipped, joined, removed, reminded, setup_edited,
    invited. Only for partner accounts; never raises."""
    pid = partner_id or (user or {}).get("partner_id")
    if not pid:
        return
    try:
        db.partner_activity.insert_one({"partner_id": pid, "user_id": (user or {}).get("id", ""),
                                        "name": (user or {}).get("name", ""), "kind": kind,
                                        "detail": (detail or "")[:300], "by": by,
                                        "at": datetime.now(timezone.utc).isoformat()})
    except Exception:
        logger.exception("[Partner] activity not recorded")


def log_for(user_id: str, kind: str, detail: str = ""):
    """log() from code that has only an account id (approvals in autonomous.py, posts in main.py)."""
    try:
        u = db.users.find_one({"id": user_id}, {"_id": 0, "id": 1, "name": 1, "partner_id": 1})
    except Exception:
        u = None
    if u and u.get("partner_id"):
        log(u, kind, detail)


# ── seats ─────────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _open_invites(partner: dict) -> list:
    return list(db.partner_invites.find({"partner_id": partner["id"], "status": "open",
                                         "expires_at": {"$gt": _now().isoformat()}}, {"_id": 0}).sort("created_at", -1))


def seats_used(partner: dict) -> tuple[int, int]:
    """(seats taken by clients, seats held by open invites)."""
    active = db.users.count_documents({"partner_id": partner["id"], "seat": "active"})
    return active, len(_open_invites(partner))


def _month_start() -> str:
    n = _now()
    return datetime(n.year, n.month, 1, tzinfo=timezone.utc).isoformat()


def _next_step(u: dict, c: dict | None, li_on: bool, waiting: int) -> tuple[str, str]:
    """What this client needs next, one thing, in the order Sam can act on it (launch audit P9). The key picks
    the reminder email (notify.partner_reminder)."""
    if u.get("seat") != "active":
        return "removed", "Removed"
    if not c:
        return "setup", "Hasn't set up"
    if not c.get("active"):
        return "drafts_off", "Morning drafts off"
    if not li_on:
        return "linkedin", "LinkedIn not connected"
    if waiting:
        return "waiting", f"{waiting} draft{'s' if waiting != 1 else ''} waiting"
    week_ago = (_now() - timedelta(days=7)).isoformat()
    if (u.get("seat_since") or "") < week_ago and not db.partner_activity.find_one(
            {"user_id": u["id"], "kind": {"$in": ["approved", "posted"]}, "at": {"$gte": week_ago}}, {"_id": 1}):
        return "quiet", "No approval in 7 days"
    return "on_track", "On track"


def _client_row(u: dict, partner: dict) -> dict:
    comps = list_companies(u["id"])
    c = comps[0] if comps else None
    gi = auth_module.get_gen_info(u["id"])
    li_on = li.connection_info(li.connection_for(c, u["id"])).get("connected", False) if c else False
    last = db.partner_activity.find_one({"user_id": u["id"], "kind": "posted"}, {"_id": 0, "at": 1}, sort=[("at", -1)])
    waiting = db.pending_posts.count_documents(_waiting(u["id"]))
    step, step_label = _next_step(u, c, li_on, waiting)
    return {
        "id": u["id"], "email": u.get("email", ""), "seat": u.get("seat", "none"), "seat_since": u.get("seat_since", ""),
        "is_team": partners.is_admin(u, partner),
        "name": (c or {}).get("name") or u.get("name", ""), "title": (c or {}).get("designation", ""),
        "profile_id": (c or {}).get("id", ""), "has_profile": bool(c),
        "daily_drafts": bool((c or {}).get("active")), "linkedin": li_on,
        "waiting": waiting, "next_step": step, "next_step_label": step_label,
        "posts_this_month": db.partner_activity.count_documents({"user_id": u["id"], "kind": "posted",
                                                                 "at": {"$gte": _month_start()}}),
        "last_post": (last or {}).get("at", ""),
        "drafts_used": gi.get("used", 0), "drafts_limit": gi.get("limit", 0),
        "ai_used": gi.get("ai_used", 0), "ai_limit": gi.get("ai_limit", 0),
    }


# ── the team's panel ──────────────────────────────────────────────────────────

@router.get("/partner/api/overview")
def overview(x_token: str = Header(None)):
    user, partner = _team(x_token)
    clients = [_client_row(u, partner) for u in
               db.users.find({"partner_id": partner["id"], "seat": {"$in": ["active", "removed"]}}, {"_id": 0})]
    clients.sort(key=lambda r: (r["seat"] != "active", -r["waiting"], r["name"].lower()))
    active, held = seats_used(partner)
    return {"brand": partner["brand"], "company": partner["company_short"], "you": user.get("email", ""),
            "seats": {"total": partner["seats"], "active": active, "invited": held,
                      "free": max(0, partner["seats"] - active - held)},
            "per_seat": {"drafts": partner["drafts_per_seat"], "ai_pictures": partner["ai_pictures_per_seat"]},
            "refills_at": partners.next_month_start().isoformat(),
            "clients": clients,
            "invites": [{**{k: i.get(k) for k in ("id", "email", "name", "created_at", "expires_at", "invited_by")},
                         "expired": i.get("expires_at", "") <= _now().isoformat(), "set_up": bool(i.get("setup"))}
                        for i in _open_invites(partner) + _expired_invites(partner)]}


def _expired_invites(partner: dict) -> list:
    """Invites that ran out unused in the last 30 days: they used to vanish, and with them the client who
    never joined (launch audit P9). They hold no seat."""
    month_ago = (_now() - timedelta(days=30)).isoformat()
    return list(db.partner_invites.find({"partner_id": partner["id"], "status": "open",
                                         "expires_at": {"$lte": _now().isoformat(), "$gt": month_ago}},
                                        {"_id": 0}).sort("expires_at", -1))


class InviteSetup(BaseModel):
    """The client's voice, set up by the partner's team before they join (main.partner_profile_from_invite)."""
    designation: str = Field("", max_length=160)     # their role line
    industry: str = Field("", max_length=400)        # their topics; without them the client sets up themselves
    tone: str = Field("", max_length=80)
    knowledge: str = Field("", max_length=4000)      # facts and writing rules
    voice_posts: str = Field("", max_length=15000)   # a few of their LinkedIn posts, with --- between them
    post_time: str = Field("09:00", max_length=5)
    timezone: str = Field("", max_length=64)


class InviteRequest(BaseModel):
    email: str
    name: str = ""
    setup: InviteSetup | None = None


@router.post("/partner/api/invites")
def invite(req: InviteRequest, request: Request, x_token: str = Header(None)):
    user, partner = _team(x_token)
    setup = {k: v.strip() for k, v in (req.setup.model_dump() if req.setup else {}).items() if isinstance(v, str)}
    return _send_invite(user, partner, req.email, req.name, setup, request)


@router.post("/partner/api/invites/{invite_id}/resend")
def resend_invite(invite_id: str, request: Request, x_token: str = Header(None)):
    """An invite that ran out unused, sent again: a new link, the same name and voice setup."""
    user, partner = _team(x_token)
    old = db.partner_invites.find_one({"id": invite_id, "partner_id": partner["id"], "status": "open"}, {"_id": 0})
    if not old:
        raise HTTPException(status_code=404, detail="Invite not found")
    sent = _send_invite(user, partner, old["email"], old.get("name", ""), old.get("setup") or {}, request)
    db.partner_invites.update_one({"id": invite_id}, {"$set": {"status": "replaced", "replaced_by": sent["id"]}})
    return sent


def _send_invite(user: dict, partner: dict, email: str, name: str, setup: dict, request: Request) -> dict:
    email = (email or "").strip().lower()
    if not _EMAIL.match(email):
        raise HTTPException(status_code=400, detail="That doesn't look like an email address.")
    if db.users.find_one({"partner_id": partner["id"], "email": email, "seat": "active"}, {"_id": 1}):
        raise HTTPException(status_code=400, detail=f"{email} already has a seat.")
    if db.partner_invites.find_one({"partner_id": partner["id"], "email": email, "status": "open",
                                    "expires_at": {"$gt": _now().isoformat()}}, {"_id": 1}):
        raise HTTPException(status_code=400, detail=f"{email} already has an open invite. Cancel it to send a new one.")
    active, held = seats_used(partner)
    if active + held >= partner["seats"]:
        raise HTTPException(status_code=400, detail=(
            f"All {partner['seats']} seats are in use or held by open invites. Remove a client, cancel an invite, "
            "or ask for more seats."))
    inv = {"id": secrets.token_hex(6), "token": secrets.token_urlsafe(24), "partner_id": partner["id"],
           "email": email, "name": (name or "").strip()[:80], "invited_by": user.get("email", ""),
           "status": "open", "created_at": _now().isoformat(),
           "expires_at": (_now() + timedelta(days=INVITE_DAYS)).isoformat()}
    if (setup or {}).get("industry"):   # topics are the least a profile needs; without them the client sets up themselves
        inv["setup"] = {**setup, "name": inv["name"]}
    db.partner_invites.insert_one({**inv})
    link = f"{partners.url_for_request(partner, request.headers.get('host', ''))}/signup?invite={inv['token']}"
    emailed = notify.partner_invite(partner, inv, link, inviter=_sender_name(user, partner))
    log(None, "invited", email, by=user.get("email", ""), partner_id=partner["id"])
    # The link comes back too, so the team can send it on WhatsApp as well
    return {"id": inv["id"], "email": email, "link": link, "emailed": emailed}


@router.post("/partner/api/invites/{invite_id}/cancel")
def cancel_invite(invite_id: str, x_token: str = Header(None)):
    user, partner = _team(x_token)
    r = db.partner_invites.update_one({"id": invite_id, "partner_id": partner["id"], "status": "open"},
                                      {"$set": {"status": "cancelled", "cancelled_at": _now().isoformat(),
                                                "cancelled_by": user.get("email", "")}})
    if not r.modified_count:
        raise HTTPException(status_code=404, detail="Invite not found")
    return {"ok": True}


@router.post("/partner/api/clients/{uid}/remove")
def remove_client(uid: str, x_token: str = Header(None)):
    """The seat is freed and the client can no longer write. Daily drafts stop and their LinkedIn connection
    is deleted (LinkedIn API terms 4.1, 4.4). Their account and posts stay until they or the partner ask for
    deletion."""
    user, partner = _team(x_token)
    c = _client(partner, uid)
    if c.get("seat") != "active":
        raise HTTPException(status_code=400, detail="This client doesn't have a seat.")
    db.users.update_one({"id": uid}, {"$set": {"seat": "removed", "seat_removed_at": _now().isoformat(),
                                               "seat_removed_by": user.get("email", "")}})
    for comp in db.companies.find({"user_id": uid}, {"_id": 0, "li_conn": 1}):
        if comp.get("li_conn"):
            li.logout(comp["li_conn"])
    li.logout(uid)
    db.companies.update_many({"user_id": uid}, {"$set": {"active": False}, "$unset": {"li_conn": ""}})
    # drafts still waiting go too, so an old approve link can't publish after they've left (3 Nov 2026 review)
    db.pending_posts.update_many({"user_id": uid, "status": "pending"}, {"$set": {"status": "discarded"}})
    log(c, "removed", by=user.get("email", ""))
    return {"ok": True}


@router.get("/partner/api/clients/{uid}")
def client_detail(uid: str, x_token: str = Header(None)):
    """One client, view only: their setup, drafts waiting for them, and what they posted."""
    _, partner = _team(x_token)
    c = _client(partner, uid)
    comps = list_companies(uid)
    prof = comps[0] if comps else {}
    keep = ("id", "name", "designation", "industry", "tone", "knowledge", "post_time", "timezone", "active")
    drafts = list(db.pending_posts.find(_waiting(uid),
                                        {"_id": 0, "id": 1, "post_text": 1, "post_type": 1, "created_at": 1,
                                         "format": 1, "slide_previews": 1}).sort("created_at", -1).limit(10))
    for d in drafts:
        d["slide_previews"] = (d.get("slide_previews") or [])[:1]   # the first slide is enough to recognise it
    return {"client": _client_row(c, partner),
            "setup": {k: prof.get(k) for k in keep if k in prof},
            "voice_samples": len(prof.get("linkedin_top_posts") or []),
            "drafts": drafts,
            "activity": list(db.partner_activity.find({"user_id": uid}, {"_id": 0}).sort("at", -1).limit(30))}


class SetupEdit(BaseModel):
    designation: str | None = None
    industry: str | None = None      # their topics
    tone: str | None = None
    knowledge: str | None = None     # their facts and writing rules


@router.put("/partner/api/clients/{uid}/setup")
def edit_setup(uid: str, req: SetupEdit, x_token: str = Header(None)):
    """The team sets up a client's voice: topics, tone, facts and rules. That's setup, not posting."""
    user, partner = _team(x_token)
    c = _client(partner, uid)
    comps = list_companies(uid)
    if not comps:
        raise HTTPException(status_code=400, detail="This client hasn't created their profile yet.")
    data = {k: v.strip() if isinstance(v, str) else v for k, v in req.model_dump().items() if v is not None}
    if "knowledge" in data:
        data["knowledge"] = data["knowledge"][:4000]
    updated = update_company(comps[0]["id"], data)
    log(c, "setup_edited", ", ".join(sorted(data)), by=user.get("email", ""))
    return {"ok": True, "setup": {k: (updated or {}).get(k) for k in ("designation", "industry", "tone", "knowledge")}}


@router.post("/partner/api/clients/{uid}/remind")
def remind(uid: str, x_token: str = Header(None)):
    user, partner = _team(x_token)
    c = _client(partner, uid)
    if c.get("seat") != "active":
        raise HTTPException(status_code=400, detail="This client doesn't have a seat.")
    row = _client_row(c, partner)
    waiting = row["waiting"]
    first_line = ""
    if row["next_step"] == "waiting":
        d = db.pending_posts.find_one(_waiting(uid), {"_id": 0, "post_text": 1}, sort=[("created_at", -1)]) or {}
        first_line = (d.get("post_text") or "").strip().split("\n")[0][:120]
    sent = notify.partner_reminder(partner, c, waiting, sent_by=_sender_name(user, partner), step=row["next_step"],
                                   first_line=first_line)
    if sent:
        log(c, "reminded", f"{waiting} waiting", by=user.get("email", ""))
    return {"sent": sent, "waiting": waiting,
            "note": "" if sent else "A reminder already went out today, or email isn't set up yet."}


@router.get("/partner/api/activity")
def activity(x_token: str = Header(None)):
    _, partner = _team(x_token)
    return list(db.partner_activity.find({"partner_id": partner["id"]}, {"_id": 0}).sort("at", -1).limit(60))


class SeatRequest(BaseModel):
    seats: int = 1
    note: str = ""


@router.post("/partner/api/seats-request")
def seats_request(req: SeatRequest, x_token: str = Header(None)):
    user, partner = _team(x_token)
    n = max(1, min(int(req.seats or 1), 100))
    notify.partner_seat_request(partner, n, (req.note or "")[:500], user.get("email", ""))
    db.partner_seat_requests.insert_one({"partner_id": partner["id"], "seats": n, "note": (req.note or "")[:500],
                                         "by": user.get("email", ""), "at": _now().isoformat()})
    return {"ok": True}


# ── a client takes their seat ─────────────────────────────────────────────────

def _invite_by_token(partner: dict, token: str) -> dict | None:
    inv = db.partner_invites.find_one({"token": token or "", "partner_id": partner["id"]}, {"_id": 0})
    if not inv or inv.get("status") != "open" or inv.get("expires_at", "") <= _now().isoformat():
        return None
    return inv


@router.get("/partner/invite-info")
def invite_info(token: str = ""):
    """For the sign-up page: whom an invite is for, so they sign up with that address."""
    partner = partners.current()
    if not partner:
        raise HTTPException(status_code=404, detail="Not found")
    inv = _invite_by_token(partner, token)
    if not inv:   # a client who already joined reopens the email as a bookmark: send them to sign in, not to CUSP
        raw = db.partner_invites.find_one({"token": token, "partner_id": partner["id"]}, {"_id": 0, "status": 1}) if token else None
        return {"valid": False, "used": bool(raw and raw.get("status") == "used")}
    return {"valid": True, "email": inv["email"], "name": inv.get("name", ""), "brand": partner["brand"]}


class JoinRequest(BaseModel):
    token: str


@router.post("/partner/join")
def join(req: JoinRequest, x_token: str = Header(None)):
    partner = partners.current()
    if not partner:
        raise HTTPException(status_code=404, detail="Not found")
    user = _user(x_token)
    if user.get("seat") == "active":
        return {"ok": True, "already": True}
    inv = _invite_by_token(partner, req.token)
    if not inv:
        raise HTTPException(status_code=400, detail=(
            f"This invite has expired or was already used. Ask {partner['company_short']} for a new one."))
    if (user.get("email") or "").lower() != inv["email"]:
        raise HTTPException(status_code=403, detail=(
            f"This invite was sent to {inv['email']}. Sign out and create your account with that address."))
    active, _ = seats_used(partner)
    if active >= partner["seats"]:
        raise HTTPException(status_code=400, detail=(
            f"All of {partner['company_short']}'s seats are taken. Ask them to free one."))
    claimed = db.partner_invites.update_one({"token": req.token, "status": "open"},
                                            {"$set": {"status": "used", "used_by": user["id"], "used_at": _now().isoformat()}})
    if not claimed.modified_count:
        raise HTTPException(status_code=400, detail="This invite was just used.")
    db.users.update_one({"id": user["id"]}, {"$set": {"seat": "active", "seat_since": _now().isoformat(),
                                                      "seat_invite": inv["id"]}})
    log({**user, "partner_id": partner["id"]}, "joined", inv["email"])
    set_up = False
    if inv.get("setup"):   # the team set up their voice: the profile is ready, and onboarding is skipped
        try:
            import main
            set_up = bool(main.partner_profile_from_invite({**user, "seat": "active"}, partner, inv["setup"]))
        except Exception:
            logger.exception(f"[partner] {partner['id']}: profile from invite {inv['id']} failed")
    return {"ok": True, "set_up": set_up}


# ── the landing page's waiting list ───────────────────────────────────────────

class WaitlistRequest(BaseModel):
    name: str = Field("", max_length=300)
    title: str = Field("", max_length=300)
    company: str = Field("", max_length=300)
    email: str = Field("", max_length=300)
    phone: str = Field("", max_length=60)
    website: str = Field("", max_length=300)   # hidden on the page: only a bot fills it in


# Emails to the partner a day, whatever the senders' addresses. Resend's plan sends 100 a day for ALL of Voyce,
# approval emails included, and a script once could spend them all here (security review, 3 Nov 2026).
# Past the cap a request is still saved, just not emailed.
WAITLIST_EMAILS_PER_DAY = 20


def _waitlist_email_allowed(partner_id: str) -> bool:
    day = _now().strftime("%Y-%m-%d")
    doc = db.usage.find_one_and_update({"_id": f"waitlist-mail:{partner_id}:{day}"},
                                       {"$inc": {"count": 1}, "$setOnInsert": {"started_at": _now()}},
                                       upsert=True, return_document=ReturnDocument.AFTER)
    return (doc or {}).get("count", 0) <= WAITLIST_EMAILS_PER_DAY


@router.post("/partner/waitlist")
def waitlist(req: WaitlistRequest, request: Request):
    """Someone asks to join on the partner's landing page (Sam, 3 Nov 2026: name, title, company, official email;
    phone optional since his 5 Nov page, as US buyers often prefer email; each one emailed to him). Saved before
    the email goes, so a failed email loses nobody."""
    partner = partners.current()
    if not partner or not partner.get("waitlist_to"):
        raise HTTPException(status_code=404, detail="Not found")
    if req.website.strip():
        return {"ok": True}   # a bot: looks accepted, nothing saved or sent
    now = _now()
    # 5 an hour per visitor; the address can't be faked here (ratelimit.client_ip: this host has no Cloudflare)
    if not ratelimit.allow(f"waitlist:{ratelimit.client_ip(request)}", 5, 3600):
        raise HTTPException(status_code=429, detail="Too many requests from here. Please try again in an hour.")
    name, title, company = (" ".join(v.split())[:120] for v in (req.name, req.title, req.company))
    email = req.email.strip().lower()[:200]
    phone = " ".join(re.sub(r"[^\d+()\- ]", "", req.phone).split())[:30]
    for ok, msg in ((len(name) >= 2, "Please add your name."), (title, "Please add your title."),
                    (company, "Please add your company."), (_EMAIL.match(email), "Please check your email address."),
                    (not phone or len(re.sub(r"\D", "", phone)) >= 8,
                     "That phone number looks too short. Leave it empty if you prefer email.")):
        if not ok:
            raise HTTPException(status_code=400, detail=msg)
    if db.partner_waitlist.find_one({"partner_id": partner["id"], "email": email,
                                     "at": {"$gte": (now - timedelta(days=1)).isoformat()}}):
        return {"ok": True}   # the same person again within a day: no second email
    entry = {"id": secrets.token_hex(8), "partner_id": partner["id"], "name": name, "title": title,
             "company": company, "email": email, "phone": phone, "at": now.isoformat(), "emailed": False}
    db.partner_waitlist.insert_one(dict(entry))
    if not _waitlist_email_allowed(partner["id"]):
        logger.warning(f"[waitlist] {partner['id']}: over {WAITLIST_EMAILS_PER_DAY} today, saved {entry['id']} unemailed")
    elif notify.partner_waitlist(partner, entry):
        db.partner_waitlist.update_one({"id": entry["id"]}, {"$set": {"emailed": True}})
    else:
        logger.error(f"[waitlist] {partner['id']}: saved {entry['id']} but the email to {partner['waitlist_to']} failed")
    return {"ok": True}


# ── Voyce's view of its partners (voyce.co.in, Voyce team) ────────────────────

def _ai_cost_inr(user_ids: list, since: str) -> dict:
    """Claude and image spend per account since a date, in rupees (llm.py logs each call)."""
    out = {uid: 0.0 for uid in user_ids}
    for row in db.ai_usage.aggregate([{"$match": {"user_id": {"$in": user_ids}, "at": {"$gte": since}}},
                                      {"$group": {"_id": "$user_id", "usd": {"$sum": "$usd"}}}]):
        out[row["_id"]] = round(row["usd"] * partners.USD_INR, 1)
    return out


@router.get("/admin/api/partners")
def admin_partners(x_token: str = Header(None)):
    _voyce_admin(x_token)
    since = _month_start()
    rows = []
    for p in partners.PARTNERS.values():
        users = list(db.users.find({"partner_id": p["id"]}, {"_id": 0}))
        seated = [u for u in users if u.get("seat") == "active"]
        cost = _ai_cost_inr([u["id"] for u in users], since)
        billed = max(p["seats"], p["min_seats"])
        first_month = not db.partner_bills.find_one({"partner_id": p["id"]}, {"_id": 1})
        per_seat = []
        for u in seated:
            gi = auth_module.get_gen_info(u["id"])
            per_seat.append({"email": u.get("email", ""), "drafts": gi.get("used", 0), "drafts_limit": gi.get("limit", 0),
                             "ai_pictures": gi.get("ai_used", 0), "ai_cost_inr": cost.get(u["id"], 0.0),
                             "near_limit": gi.get("limit", 0) and gi.get("used", 0) >= 0.8 * gi["limit"]})
        total_cost = round(sum(cost.values()), 1)
        bill = billed * p["rate_inr"] + (p["setup_inr"] if first_month else 0)
        rows.append({"id": p["id"], "brand": p["brand"], "company": p["company"], "address": p["hosts"][0],
                     "seats_plan": p["seats"], "seats_billed": billed, "seats_active": len(seated),
                     "invites_open": len(_open_invites(p)), "rate_inr": p["rate_inr"],
                     "bill_this_month_inr": bill, "first_bill_adds_setup": first_month,
                     "ai_cost_this_month_inr": total_cost, "margin_inr": round(bill - total_cost, 1),
                     "seats": sorted(per_seat, key=lambda r: -r["drafts"])})
    return rows
