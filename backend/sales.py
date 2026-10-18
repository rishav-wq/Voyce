"""Voyce Sales: the team's outreach tracker (cold calls to firm owners, LinkedIn DMs with
follow-ups), served at /sales for the Voyce team only.

Who gets in: anyone in ADMIN_EMAILS, plus users whose record has `sales_access: true`.
Every team member sees and edits every lead; the server stamps each change into the
lead's history with who made it. The stage logic (which step comes next, when the next
follow-up is due) lives in the page; the server only checks that what it stores is sane.

Details come from Apollo (people/match, lookup only: nothing is added to Apollo lists or
sequences) with APOLLO_API_KEY: the work email and the firm's published line on "Find
email & firm phone", and the person's own number only on an explicit "Get their number".
"""
import os
import re
import secrets
from datetime import datetime, timezone

import requests
from fastapi import APIRouter, Header, HTTPException
from pydantic import BaseModel

import auth as auth_module
import db
import ratelimit

router = APIRouter()

STAGES = {"tocall", "try1", "try2", "try3", "spoke", "sample",
          "new", "dm", "fu1", "fu2", "fu3", "replied", "number", "called",
          "meeting", "won", "lost"}
LOOKUP_STATES = {"", "done", "none"}
# field -> max length; anything else a client sends is dropped
TEXT_FIELDS = {"name": 120, "role": 160, "company": 160, "city": 80, "linkedin": 300, "website": 300,
               "phone": 40, "firm_phone": 60, "email": 160, "source": 60, "segment": 60,
               "lost_reason": 120, "notes": 4000, "research_url": 500}
DATE_FIELDS = {"next_due", "dm_at"}            # "" or YYYY-MM-DD
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$")
HISTORY_KEEP = 200


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def is_member(user: dict | None) -> bool:
    if not user:
        return False
    if (user.get("email") or "").lower() in auth_module._ADMIN_EMAILS:
        return True
    return bool(user.get("sales_access"))


def _team_user(x_token: str | None) -> dict:
    user = auth_module.get_user_by_token(x_token or "")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not is_member(user):
        raise HTTPException(status_code=403, detail="SALES_ONLY")
    return user


def _who(user: dict) -> str:
    return (user.get("name") or (user.get("email") or "").split("@")[0] or "Teammate").strip()[:80]


def _clean(fields: dict, user: dict) -> dict:
    """Keep only known fields, trimmed and checked. Raises 400 on a value that can't be stored."""
    out = {}
    for k, v in (fields or {}).items():
        if k in TEXT_FIELDS:
            out[k] = str(v if v is not None else "").strip()[:TEXT_FIELDS[k]]
        elif k in DATE_FIELDS:
            v = str(v or "").strip()
            if v and not _DATE.match(v):
                raise HTTPException(status_code=400, detail=f"{k} must be a date (YYYY-MM-DD)")
            out[k] = v
        elif k == "meeting_at":
            v = str(v or "").strip()
            if v and not _DATETIME.match(v):
                raise HTTPException(status_code=400, detail="meeting_at must be a date and time")
            out[k] = v
        elif k == "stage":
            if v not in STAGES:
                raise HTTPException(status_code=400, detail="Unknown stage")
            out[k] = v
        elif k == "owner":
            # Only "me" (assign to yourself) or "" (unassign): owners are always real teammates.
            if v == "me":
                out["owner"], out["owner_name"] = user["id"], _who(user)
            elif not v:
                out["owner"], out["owner_name"] = "", ""
    return out


def _entry(user: dict, text: str, stage: str = "") -> dict:
    return {"at": _now(), "by": user.get("id"), "by_name": _who(user), "text": str(text or "")[:300], "stage": stage}


def _public(doc: dict | None) -> dict | None:
    if not doc:
        return None
    doc = {k: v for k, v in doc.items() if k != "_id"}
    return doc


# ── Leads ─────────────────────────────────────────────────────────────────────
class LeadIn(BaseModel):
    fields: dict = {}
    note: str = ""


@router.get("/sales/me")
def sales_me(x_token: str = Header(None)):
    user = _team_user(x_token)
    return {"id": user["id"], "name": _who(user), "email": user.get("email", "")}


@router.get("/sales/leads")
def list_leads(x_token: str = Header(None)):
    _team_user(x_token)
    return [_public(d) for d in db.sales_leads.find({}, {"_id": 0}).sort("created_at", 1)]


@router.post("/sales/leads")
def create_lead(body: LeadIn, x_token: str = Header(None)):
    user = _team_user(x_token)
    ratelimit.allow(f"sales:{user['id']}", 120, 60) or _too_fast()
    f = _clean(body.fields, user)
    if not f.get("name"):
        raise HTTPException(status_code=400, detail="A lead needs a name")
    f.setdefault("stage", "new")
    now = _now()
    doc = {**{k: "" for k in TEXT_FIELDS}, "next_due": "", "dm_at": "", "meeting_at": "",
           "lookup": "", "lookup_note": "", "lookup_at": "", **f,
           "id": "l_" + secrets.token_urlsafe(9), "owner": user["id"], "owner_name": _who(user),
           "created_by": user["id"], "created_at": now, "updated_at": now,
           "history": [_entry(user, body.note or "Added", f["stage"])]}
    db.sales_leads.insert_one(dict(doc))
    return _public(doc)


@router.patch("/sales/leads/{lead_id}")
def update_lead(lead_id: str, body: LeadIn, x_token: str = Header(None)):
    user = _team_user(x_token)
    ratelimit.allow(f"sales:{user['id']}", 120, 60) or _too_fast()
    cur = db.sales_leads.find_one({"id": lead_id}, {"_id": 0, "stage": 1, "phone": 1})
    if not cur:
        raise HTTPException(status_code=404, detail="That lead was deleted")
    f = _clean(body.fields, user)
    if "name" in f and not f["name"]:
        raise HTTPException(status_code=400, detail="A lead needs a name")
    if "phone" in f and f["phone"] != (cur.get("phone") or ""):
        f.update(phone_source="", phone_type="", phone_note="")   # a number someone typed in, not Apollo's
    f["updated_at"] = _now()
    op = {"$set": f}
    if body.note.strip():
        op["$push"] = {"history": {"$each": [_entry(user, body.note, f.get("stage") or cur.get("stage", ""))],
                                   "$slice": -HISTORY_KEEP}}
    db.sales_leads.update_one({"id": lead_id}, op)
    return _public(db.sales_leads.find_one({"id": lead_id}, {"_id": 0}))


@router.delete("/sales/leads/{lead_id}")
def delete_lead(lead_id: str, x_token: str = Header(None)):
    _team_user(x_token)
    db.sales_leads.delete_one({"id": lead_id})
    return {"ok": True}


def _too_fast():
    raise HTTPException(status_code=429, detail="Too many changes at once. Wait a few seconds and try again.")


# ── Scripts ───────────────────────────────────────────────────────────────────
class ScriptsIn(BaseModel):
    items: list = []


@router.get("/sales/scripts")
def get_scripts(x_token: str = Header(None)):
    _team_user(x_token)
    doc = db.sales_config.find_one({"id": "scripts"}, {"_id": 0}) or {}
    return {"items": doc.get("items") or []}


@router.put("/sales/scripts")
def put_scripts(body: ScriptsIn, x_token: str = Header(None)):
    user = _team_user(x_token)
    items = []
    for it in body.items[:40]:
        if not isinstance(it, dict):
            continue
        key = re.sub(r"[^a-z0-9_-]", "", str(it.get("key") or "").lower())[:40] or ("custom-" + secrets.token_hex(3))
        items.append({"key": key, "title": str(it.get("title") or "Untitled")[:120],
                      "when": str(it.get("when") or "")[:200], "body": str(it.get("body") or "")[:4000]})
    db.sales_config.update_one({"id": "scripts"}, {"$set": {"id": "scripts", "items": items, "updated_at": _now(),
                                                    "updated_by": user["id"]}}, upsert=True)
    return {"items": items}


# ── Apollo lookup ─────────────────────────────────────────────────────────────
# Lookup only: nothing is added to Apollo lists or sequences, and calls show in Apollo
# under the API key's own name. A person's own number comes only from an explicit "Get
# their number" (about 9 credits when a mobile is found); everything else is 1 at most.
APOLLO = "https://api.apollo.io/api/v1"
LABELS = {"email": "work email", "firm_phone": "firm line", "role": "role", "company": "company", "city": "city"}
PHONE_WAIT_MINUTES = 30
PHONE_PREFERENCE = {"mobile": 0, "work_direct": 1, "other": 2}


def _apollo_key() -> str:
    key = os.getenv("APOLLO_API_KEY", "").strip()
    if not key:
        raise HTTPException(status_code=503, detail="Apollo isn't connected yet. Add APOLLO_API_KEY to the server's settings.")
    return key


def _apollo_match(lead: dict, reveal_phone: bool = False) -> tuple[dict | None, str, str]:
    """(found fields, or None on an Apollo error; the error text; the request_id of a phone reveal)."""
    key = _apollo_key()
    body = {}
    if lead.get("linkedin"):
        u = lead["linkedin"].strip()
        body["linkedin_url"] = u if u.startswith("http") else "https://" + u
    parts = (lead.get("name") or "").split(" ", 1)
    if len(parts) == 2:
        body["first_name"], body["last_name"] = parts
    if lead.get("company"):
        body["organization_name"] = lead["company"]
    if reveal_phone:
        # poll_only: Apollo returns a request_id to collect the numbers with later
        # (free), so no public webhook is needed.
        body.update(reveal_phone_number=True, poll_only=True)
    try:
        r = requests.post(f"{APOLLO}/people/match", json=body, timeout=25,
                          headers={"X-Api-Key": key, "Content-Type": "application/json", "Cache-Control": "no-cache"})
    except requests.RequestException:
        return None, "Apollo didn't answer. Try again in a minute.", ""
    if r.status_code == 429:
        return None, "Apollo is rate-limiting us. Try again in a minute.", ""
    if not r.ok:
        return None, f"Apollo refused the lookup ({r.status_code}).", ""
    data = r.json() or {}
    p = data.get("person") or {}
    request_id = str(data.get("request_id") or p.get("request_id") or "")
    if not p:
        return {}, "Apollo found no match", request_id
    org = p.get("organization") or {}
    ph = org.get("primary_phone") or {}
    email = p.get("email") or ""
    if "not_unlocked" in email or "@domain.com" in email:
        email = ""
    name = (p.get("name") or f"{p.get('first_name') or ''} {p.get('last_name') or ''}").strip()
    return {"name": name, "linkedin": p.get("linkedin_url") or "", "email": email,
            "firm_phone": ph.get("sanitized_number") or ph.get("number") or org.get("sanitized_phone") or org.get("phone") or "",
            "role": p.get("title") or "", "company": org.get("name") or "", "city": p.get("city") or ""}, "", request_id


def _fill_blanks(lead: dict, found: dict) -> dict:
    """Apollo only fills what the team left empty, never overwrites."""
    return {k: str(v)[:TEXT_FIELDS[k]] for k, v in found.items() if k in LABELS and v and not str(lead.get(k) or "").strip()}


def _lead_or_404(lead_id: str) -> dict:
    lead = db.sales_leads.find_one({"id": lead_id}, {"_id": 0})
    if not lead:
        raise HTTPException(status_code=404, detail="That lead was deleted")
    return lead


def _write(lead_id: str, fields: dict, user: dict | None, note: str, stage: str) -> dict:
    op = {"$set": {**fields, "updated_at": _now()}}
    if note:
        e = _entry(user, note, stage) if user else {"at": _now(), "by": None, "by_name": "Apollo", "text": note[:300], "stage": stage}
        op["$push"] = {"history": {"$each": [e], "$slice": -HISTORY_KEEP}}
    db.sales_leads.update_one({"id": lead_id}, op)
    return _public(db.sales_leads.find_one({"id": lead_id}, {"_id": 0}))


@router.post("/sales/leads/{lead_id}/lookup")
def lookup_lead(lead_id: str, x_token: str = Header(None)):
    """Work email and the firm's published line (1 credit at most)."""
    user = _team_user(x_token)
    ratelimit.allow(f"sales-lookup:{user['id']}", 20, 60) or _too_fast()
    lead = _lead_or_404(lead_id)
    if not (lead.get("linkedin") or (lead.get("name") and lead.get("company"))):
        raise HTTPException(status_code=400, detail="Add their LinkedIn link or company first.")
    found, err, _ = _apollo_match(lead)
    if found is None:
        raise HTTPException(status_code=502, detail=err)
    fill = _fill_blanks(lead, found)
    note = ("Apollo: found " + ", ".join(LABELS[k] for k in fill)) if fill else (err or "Apollo: nothing new")
    return _write(lead_id, {**fill, "lookup": "done" if found else "none", "lookup_note": note, "lookup_at": _now()},
                  user, note, lead.get("stage", ""))


class EnrichIn(BaseModel):
    linkedin: str = ""
    name: str = ""
    company: str = ""


@router.post("/sales/enrich")
def enrich(body: EnrichIn, x_token: str = Header(None)):
    """Fill the new-lead form from a LinkedIn link. Saves nothing (1 credit at most)."""
    user = _team_user(x_token)
    ratelimit.allow(f"sales-lookup:{user['id']}", 20, 60) or _too_fast()
    if not (body.linkedin.strip() or (body.name.strip() and body.company.strip())):
        raise HTTPException(status_code=400, detail="Paste their LinkedIn link first.")
    found, err, _ = _apollo_match({"linkedin": body.linkedin.strip()[:300], "name": body.name.strip()[:120],
                                   "company": body.company.strip()[:160]})
    if found is None:
        raise HTTPException(status_code=502, detail=err)
    if not found:
        raise HTTPException(status_code=404, detail="Apollo has no one at that link. Check it, or fill the form in yourself.")
    return {k: str(found.get(k) or "")[:TEXT_FIELDS[k]] for k in ("name", "role", "company", "city", "linkedin", "email", "firm_phone")}


@router.post("/sales/leads/{lead_id}/phone")
def request_phone(lead_id: str, x_token: str = Header(None)):
    """Ask Apollo for the person's own number (about 9 credits when a mobile is found).
    It arrives within minutes; /phone-check collects it."""
    user = _team_user(x_token)
    ratelimit.allow(f"sales-lookup:{user['id']}", 20, 60) or _too_fast()
    lead = _lead_or_404(lead_id)
    if (lead.get("phone") or "").strip():
        raise HTTPException(status_code=400, detail="They already have a number on file.")
    if lead.get("phone_reveal") == "pending":
        return lead
    if not (lead.get("linkedin") or (lead.get("name") and lead.get("company"))):
        raise HTTPException(status_code=400, detail="Add their LinkedIn link or company first.")
    found, err, request_id = _apollo_match(lead, reveal_phone=True)
    if found is None:
        raise HTTPException(status_code=502, detail=err)
    if not found or not request_id:
        note = "Apollo: no number for them" if found else (err or "Apollo found no match")
        return _write(lead_id, {"phone_reveal": "none", "phone_note": note}, user, note, lead.get("stage", ""))
    fill = _fill_blanks(lead, found)
    also = f" (and found their {', '.join(LABELS[k] for k in fill)})" if fill else ""
    return _write(lead_id, {**fill, "phone_reveal": "pending", "phone_request_id": request_id, "phone_requested_at": _now(),
                            "phone_note": "Apollo is finding their number"},
                  user, "Asked Apollo for their number" + also, lead.get("stage", ""))


def _best_number(result: dict) -> tuple[str, str]:
    nums = [n for person in (result.get("people") or []) for n in (person.get("phone_numbers") or [])]
    nums = [n for n in nums if (n.get("sanitized_number") or n.get("raw_number")) and n.get("status_cd") != "invalid_number"]
    if not nums:
        return "", ""
    nums.sort(key=lambda n: PHONE_PREFERENCE.get(n.get("type_cd") or "", 3))
    return (nums[0].get("raw_number") or nums[0].get("sanitized_number")), (nums[0].get("type_cd") or "")


@router.post("/sales/leads/{lead_id}/phone-check")
def check_phone(lead_id: str, x_token: str = Header(None)):
    """Collect a requested number (free). The page calls this while a request is pending."""
    _team_user(x_token)
    lead = _lead_or_404(lead_id)
    if lead.get("phone_reveal") != "pending" or not lead.get("phone_request_id"):
        return lead
    try:
        requested = datetime.fromisoformat(lead.get("phone_requested_at") or _now())
    except ValueError:
        requested = datetime.now(timezone.utc)
    timed_out = (datetime.now(timezone.utc) - requested).total_seconds() > PHONE_WAIT_MINUTES * 60
    stage = lead.get("stage", "")
    try:
        r = requests.get(f"{APOLLO}/webhook_result/{lead['phone_request_id']}", timeout=20,
                         headers={"X-Api-Key": _apollo_key(), "Cache-Control": "no-cache"})
    except requests.RequestException:
        return lead
    try:
        data = r.json() or {}
    except ValueError:
        data = {}
    pending = (r.status_code == 404 and data.get("error_code") == "result_pending") or (r.ok and data.get("webhook_status") == "in_progress")
    if pending:
        if timed_out:
            return _write(lead_id, {"phone_reveal": "none", "phone_note": "Apollo didn't return a number"}, None,
                          "Apollo: no number came back", stage)
        return lead
    if not r.ok or data.get("webhook_status") == "failed":
        return _write(lead_id, {"phone_reveal": "none", "phone_note": "Apollo couldn't find their number"}, None,
                      "Apollo couldn't find their number", stage)
    number, kind = _best_number(data.get("webhook_result") or {})
    if not number:
        return _write(lead_id, {"phone_reveal": "none", "phone_note": "Apollo has no number for them"}, None,
                      "Apollo: no number for them", stage)
    label = {"mobile": "mobile", "work_direct": "direct line"}.get(kind, "number")
    fields = {"phone_reveal": "done", "phone_source": "Apollo", "phone_type": kind, "phone_note": f"From Apollo ({label})"}
    if not (lead.get("phone") or "").strip():
        fields["phone"] = number[:TEXT_FIELDS["phone"]]
    return _write(lead_id, fields, None, f"Apollo: found their {label}", stage)
