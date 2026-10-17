"""Transactional email: the only way a delegating user learns that autopilot
needs them. Sent through Resend from notify.voyce.co.in.

Every send is keyed in db.notifications so a retry, a catch-up run or a second
instance can never email the same thing twice. Nothing here raises: an email
problem must never stop a post from being written or published.
"""
import html
import logging
import os
from datetime import datetime, timedelta

import requests

import db

logger = logging.getLogger(__name__)

_API_URL   = "https://api.resend.com/emails"
_FROM      = os.getenv("NOTIFY_FROM", "Voyce <posts@notify.voyce.co.in>")
_REPLY_TO  = os.getenv("NOTIFY_REPLY_TO", "hello@voyce.co.in")
APP_URL    = os.getenv("APP_URL", "https://voyce.co.in").rstrip("/")
_QUEUE_URL = f"{APP_URL}/setup"


def _already_sent(key: str) -> bool:
    try:
        return db.notifications.find_one({"_id": key}, {"_id": 1}) is not None
    except Exception:
        logger.exception("[notify] dedupe lookup failed")
        return True   # if we can't tell, stay quiet rather than spam


def _mark_sent(key: str, user_id: str, kind: str):
    try:
        db.notifications.update_one(
            {"_id": key},
            {"$setOnInsert": {"user_id": user_id, "kind": kind,
                              "sent_at": datetime.now().isoformat()}},
            upsert=True)
    except Exception:
        logger.exception("[notify] could not record sent email")


def _user_email(user_id: str) -> str:
    try:
        u = db.users.find_one({"id": user_id}, {"email": 1}) or {}
        return (u.get("email") or "").strip()
    except Exception:
        return ""


def _layout(heading: str, paragraphs: list[str], button: tuple[str, str] | None = None,
            quote: str = "", footer: str = "") -> str:
    """Plain on purpose: one heading, a few lines, at most one button. Heavy
    templates land in Promotions; this should read like a note from a person."""
    parts = [f'<p style="font-size:17px;font-weight:600;color:#16181d;margin:0 0 14px;">{html.escape(heading)}</p>']
    for p in paragraphs:
        parts.append(f'<p style="font-size:15px;line-height:1.55;color:#333;margin:0 0 14px;">{p}</p>')
    if quote:
        parts.append(
            '<div style="white-space:pre-wrap;font-size:14px;line-height:1.55;color:#333;'
            'border-left:3px solid #24365e;background:#f6f7f9;padding:12px 14px;margin:0 0 18px;">'
            f'{html.escape(quote)}</div>')
    if button:
        label, url = button
        parts.append(
            f'<p style="margin:0 0 20px;"><a href="{html.escape(url)}" style="display:inline-block;'
            'background:#24365e;color:#ffffff;text-decoration:none;font-weight:600;font-size:15px;'
            f'padding:11px 20px;border-radius:8px;">{html.escape(label)}</a></p>')
    if footer:
        parts.append(f'<p style="font-size:12.5px;line-height:1.5;color:#7a8190;margin:18px 0 0;">{footer}</p>')
    return ('<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Inter,Arial,sans-serif;'
            'max-width:560px;margin:0 auto;padding:8px 4px;">' + "".join(parts) + "</div>")


def send(user_id: str, key: str, kind: str, subject: str, text: str, html_body: str) -> bool:
    """Send once per key. Returns True only when an email actually went out."""
    try:
        api_key = os.getenv("RESEND_API_KEY", "").strip()
        if not api_key:
            logger.warning(f"[notify] RESEND_API_KEY unset — skipped '{kind}' email")
            return False
        to = _user_email(user_id)
        if not to or _already_sent(key):
            return False
        r = requests.post(_API_URL, timeout=15, headers={"Authorization": f"Bearer {api_key}"}, json={
            "from": _FROM, "to": [to], "reply_to": _REPLY_TO,
            "subject": subject, "text": text, "html": html_body,
        })
        if not r.ok:
            logger.error(f"[notify] Resend rejected '{kind}': {r.status_code} {r.text[:200]}")
            return False
        _mark_sent(key, user_id, kind)
        logger.info(f"[notify] sent '{kind}' email ({key})")
        return True
    except Exception:
        logger.exception(f"[notify] '{kind}' email failed")
        return False


# ── Events ────────────────────────────────────────────────────────────────────

def _sources_line(sources: list) -> tuple[str, str]:
    s = [x for x in (sources or []) if x.get("url")][:2]
    if not s:
        return "", ""
    text = "Based on: " + "; ".join(f"{x.get('title') or x['url']} ({x['url']})" for x in s)
    links = ", ".join(f'<a href="{html.escape(x["url"])}" style="color:#24365e;">'
                      f'{html.escape(x.get("title") or x["url"])}</a>' for x in s)
    return text, f"Based on: {links}"


def post_ready(company: dict, pending_id: str, post_type: str, post_text: str,
               sources: list | None = None, held_reason: str = "") -> bool:
    """A post is waiting for the user's OK — either because they asked to approve
    every post, or because it failed the fact check and was held back."""
    name = company.get("name", "your profile")
    preview = (post_text or "").strip()
    if len(preview) > 700:
        preview = preview[:700].rstrip() + "…"
    src_text, src_html = _sources_line(sources)
    if held_reason:
        subject = f"Check this post before it goes out ({name})"
        intro = (f"Voyce wrote today's {html.escape(post_type)} for {html.escape(name)}, but held it back "
                 f"instead of posting it: {html.escape(held_reason)}")
    else:
        subject = f"Your post for {name} is ready to approve"
        intro = (f"Voyce wrote today's {html.escape(post_type)} for {html.escape(name)}. "
                 "It won't go out until you approve it.")
    paras = [intro] + ([src_html] if src_html else [])
    text = (f"{html.unescape(intro)}\n\n{preview}\n\n"
            + (f"{src_text}\n\n" if src_text else "")
            + f"Review and approve: {_QUEUE_URL}\n\nPosts you don't approve expire after 48 hours.")
    body = _layout("Ready for your OK" if not held_reason else "Held for your review",
                   paras, button=("Review and approve", _QUEUE_URL), quote=preview,
                   footer="Posts you don't approve expire after 48 hours, so stale news never goes out.")
    return send(company.get("user_id", ""), f"ready:{pending_id}", "post_ready", subject, text, body)


def post_failed(company: dict, error: str, local_day: str) -> bool:
    """Today's run produced nothing on LinkedIn. One email per profile per day."""
    name = company.get("name", "your profile")
    uid = company.get("user_id", "")
    if "LinkedIn not connected" in (error or "") and company.get("li_conn"):
        # The profile posts to someone else's LinkedIn (a client's): they have to
        # reconnect, through a fresh invite link from the owner.
        bucket = datetime.now().toordinal() // 3
        subject = f"{name} needs its LinkedIn reconnected"
        intro = (f"Today's post for {html.escape(name)} couldn't go out: the LinkedIn account connected to "
                 "this profile has expired or was disconnected. LinkedIn asks every app to reconnect about "
                 "every 60 days.")
        nxt = "Open the profile in Voyce, create a new invite link, and send it to them to reconnect."
        text = f"{html.unescape(intro)}\n\n{nxt}\n\nOpen Voyce: {_QUEUE_URL}"
        body = _layout("A profile's LinkedIn needs reconnecting", [intro, nxt], button=("Open Voyce", _QUEUE_URL))
        return send(uid, f"li_reconnect:{company.get('li_conn')}:{bucket}", "li_reconnect", subject, text, body)
    if "LinkedIn not connected" in (error or ""):
        # One reminder every few days, not one per profile per day.
        bucket = datetime.now().toordinal() // 3
        subject = "Reconnect LinkedIn so Voyce can keep posting"
        intro = (f"Today's post for {html.escape(name)} couldn't go out because your LinkedIn "
                 "connection has expired or was removed. LinkedIn asks every app to reconnect "
                 "about every 60 days. It takes about ten seconds.")
        text = f"{html.unescape(intro)}\n\nReconnect: {_QUEUE_URL}"
        body = _layout("Your LinkedIn connection needs a refresh", [intro],
                       button=("Reconnect LinkedIn", _QUEUE_URL))
        return send(uid, f"li_reconnect:{uid}:{bucket}", "li_reconnect", subject, text, body)
    subject = f"Today's post for {name} didn't go out"
    reason = (error or "an unexpected error").strip()
    if len(reason) > 240:
        reason = reason[:240] + "…"
    intro = (f"Voyce tried to write and publish today's post for {html.escape(name)}, but it failed: "
             f"<em>{html.escape(reason)}</em>")
    nxt = "You can try again with <strong>Post now</strong> on the profile, or it will run again at the next scheduled time."
    text = f"{html.unescape(intro)}\n\n{html.unescape(nxt)}\n\nOpen Voyce: {_QUEUE_URL}"
    body = _layout("A post didn't go out", [intro, nxt], button=("Open Voyce", _QUEUE_URL))
    return send(uid, f"failed:{company.get('id')}:{local_day}", "post_failed", subject, text, body)


def client_connected(company: dict, li_name: str = "") -> bool:
    """Someone connected their LinkedIn to one of the owner's profiles."""
    uid = company.get("user_id", "")
    prof = company.get("name", "your profile")
    who = li_name or "The person you invited"
    subject = f"{who} connected their LinkedIn to {prof}"
    intro = (f"{html.escape(who)} connected their LinkedIn account to the Voyce profile "
             f"<strong>{html.escape(prof)}</strong>. Posts for this profile will now publish to their LinkedIn.")
    nxt = ("If daily posting is set to ask first, you'll approve each post before it goes out. "
           "You can switch the profile back to your own LinkedIn at any time.")
    text = f"{html.unescape(intro).replace('<strong>', '').replace('</strong>', '')}\n\n{nxt}\n\nOpen Voyce: {_QUEUE_URL}"
    body = _layout("A LinkedIn account was connected", [intro, nxt], button=("Open Voyce", _QUEUE_URL))
    return send(uid, f"client_connected:{company.get('id')}:{datetime.now().isoformat()[:16]}",
                "client_connected", subject, text, body)


def autopilot_paused(company: dict, reason: str = "") -> bool:
    """Autopilot is still switched on but can't run — usually the Pro pass ended."""
    uid = company.get("user_id", "")
    try:
        u = db.users.find_one({"id": uid}, {"plan_expires_at": 1}) or {}
    except Exception:
        u = {}
    marker = (u.get("plan_expires_at") or "free")[:10]
    subject = "Voyce autopilot is paused"
    if reason == "pass_limit":
        intro = ("You've used all the generations included in this Pro pass, so Voyce has paused "
                 "your daily posts. Your profiles, voice and schedule are all still saved.")
        nxt = "Renew any time to get more — renewing early adds to the days you have left."
    else:
        intro = ("Your Pro pass has ended, so Voyce has stopped writing your daily posts. "
                 "Your profiles, voice and schedule are all still saved.")
        nxt = "Renew any time and posting picks up again at the next scheduled time."
    text = f"{intro}\n\n{nxt}\n\nRenew: {_QUEUE_URL}"
    body = _layout("Autopilot is paused", [intro, nxt], button=("Renew Pro", _QUEUE_URL))
    return send(uid, f"paused:{reason or 'expired'}:{uid}:{marker}", "autopilot_paused", subject, text, body)


def sweep_expiries():
    """Daily: warn before the two things that silently stop autopilot — the
    LinkedIn connection (~60 days) and the Pro pass — while there's time to act."""
    import linkedin as li
    now = datetime.now()
    try:
        users = list(db.users.find({}, {"_id": 0, "id": 1, "plan": 1, "plan_expires_at": 1}))
        active_owners = {c.get("user_id") for c in db.companies.find({"active": True}, {"user_id": 1})}
    except Exception:
        logger.exception("[notify] expiry sweep could not read users")
        return
    for u in users:
        uid = u.get("id")
        if not uid:
            continue
        try:
            # LinkedIn token: only matters to someone whose autopilot is on.
            if uid in active_owners:
                exp = li.token_expires_at(uid)
                if exp:
                    exp_naive = exp.astimezone().replace(tzinfo=None) if exp.tzinfo else exp
                    days = (exp_naive - now).days
                    if 0 <= days <= 7:
                        when = "tomorrow" if days <= 1 else f"in {days} days"
                        intro = (f"Your LinkedIn connection expires {when}. After that, Voyce can't publish "
                                 "for you until you reconnect. Reconnecting now takes about ten seconds.")
                        send(uid, f"li_expiring:{uid}:{exp_naive.date().isoformat()}", "li_expiring",
                             f"Your LinkedIn connection expires {when}",
                             f"{intro}\n\nReconnect: {_QUEUE_URL}",
                             _layout("Reconnect LinkedIn before it expires", [intro],
                                     button=("Reconnect LinkedIn", _QUEUE_URL)))
            # Pro pass: a one-off 31-day pass, so nothing renews unless they act.
            if u.get("plan") == "pro" and u.get("plan_expires_at"):
                pexp = datetime.fromisoformat(u["plan_expires_at"])
                if pexp.tzinfo:
                    pexp = pexp.astimezone().replace(tzinfo=None)
                left = pexp - now
                for limit, tag in ((timedelta(days=1), "1"), (timedelta(days=5), "5")):
                    if timedelta(0) <= left <= limit:
                        when = "tomorrow" if tag == "1" else f"on {pexp.strftime('%b')} {pexp.day}"
                        intro = (f"Your Voyce Pro pass ends {when}. Passes never auto-charge, so autopilot "
                                 "stops then unless you renew. Renewing early adds to the days you have left.")
                        send(uid, f"pro_exp{tag}:{uid}:{pexp.date().isoformat()}", f"pro_expiring_{tag}",
                             f"Your Voyce Pro pass ends {when}",
                             f"{intro}\n\nRenew: {_QUEUE_URL}",
                             _layout("Your Pro pass is ending", [intro], button=("Renew Pro", _QUEUE_URL)))
                        break
        except Exception:
            logger.exception(f"[notify] expiry sweep failed for {uid}")

    # Profiles that post to someone else's LinkedIn: warn the owner before that
    # connection lapses, so they can send a fresh invite in time.
    try:
        profile_conns = list(db.companies.find({"active": True, "li_conn": {"$exists": True}},
                                               {"_id": 0, "id": 1, "name": 1, "user_id": 1, "li_conn": 1}))
    except Exception:
        logger.exception("[notify] expiry sweep could not read profile connections")
        return
    for c in profile_conns:
        try:
            conn, uid = c.get("li_conn"), c.get("user_id")
            exp = li.token_expires_at(conn) if conn else None
            if not exp or not uid:
                continue
            exp_naive = exp.astimezone().replace(tzinfo=None) if exp.tzinfo else exp
            days = (exp_naive - now).days
            if 0 <= days <= 7:
                when = "tomorrow" if days <= 1 else f"in {days} days"
                prof = c.get("name", "a profile")
                intro = (f"The LinkedIn account connected to {html.escape(prof)} expires {when}. After that, "
                         "Voyce can't publish for this profile. Send them a new invite link from the profile "
                         "card so they can reconnect.")
                send(uid, f"li_expiring:{conn}:{exp_naive.date().isoformat()}", "li_expiring",
                     f"{prof}: LinkedIn connection expires {when}",
                     f"{html.unescape(intro)}\n\nOpen Voyce: {_QUEUE_URL}",
                     _layout("A profile's LinkedIn connection is expiring", [intro],
                             button=("Open Voyce", _QUEUE_URL)))
        except Exception:
            logger.exception(f"[notify] profile connection sweep failed for {c.get('id')}")
