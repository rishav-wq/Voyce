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
import partners

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
            quote: str = "", footer: str = "", partner: dict | None = None) -> str:
    """Plain on purpose: one heading, a few lines, at most one button. Heavy
    templates land in Promotions; this should read like a note from a person.
    A partner's email uses the partner's colour for the bar and the button."""
    accent = (partner or {}).get("theme", {}).get("orange", "#24365e") if partner else "#24365e"
    parts = [f'<p style="font-size:17px;font-weight:600;color:#16181d;margin:0 0 14px;">{html.escape(heading)}</p>']
    for p in paragraphs:
        parts.append(f'<p style="font-size:15px;line-height:1.55;color:#333;margin:0 0 14px;">{p}</p>')
    if quote:
        parts.append(
            '<div style="white-space:pre-wrap;font-size:14px;line-height:1.55;color:#333;'
            f'border-left:3px solid {accent};background:#f6f7f9;padding:12px 14px;margin:0 0 18px;">'
            f'{html.escape(quote)}</div>')
    if button:
        label, url = button
        parts.append(
            f'<p style="margin:0 0 20px;"><a href="{html.escape(url)}" style="display:inline-block;'
            f'background:{accent};color:#ffffff;text-decoration:none;font-weight:600;font-size:15px;'
            f'padding:11px 20px;border-radius:8px;">{html.escape(label)}</a></p>')
    if footer:
        parts.append(f'<p style="font-size:12.5px;line-height:1.5;color:#7a8190;margin:18px 0 0;">{footer}</p>')
    return ('<div style="font-family:-apple-system,BlinkMacSystemFont,\'Segoe UI\',Inter,Arial,sans-serif;'
            'max-width:560px;margin:0 auto;padding:8px 4px;">' + "".join(parts) + "</div>")


def _partner_of(user_id: str) -> dict | None:
    try:
        return partners.for_user(db.users.find_one({"id": user_id}, {"partner_id": 1}))
    except Exception:
        return None


def _in_partner_name(partner: dict, subject: str, text: str, html_body: str) -> tuple:
    """The events below are written for Voyce; a partner's client gets them in the partner's name, with
    links to the partner's address and its colours."""
    import re
    import branding
    base = partners.base_url(partner)
    word = re.compile(r"\bVoyce\b")
    swap = lambda t: word.sub(partner["brand"], (t or "").replace(APP_URL, base))
    return swap(subject), swap(text), branding._colours(swap(html_body), partner)


def app_url(partner: dict | None = None) -> str:
    """Where links in an email point: the partner's address for its clients, voyce.co.in otherwise."""
    return partners.base_url(partner) if partner else APP_URL


def send(user_id: str, key: str, kind: str, subject: str, text: str, html_body: str,
         to: str = "", partner: dict | None = None, reply_to: str = "") -> bool:
    """Send once per key. Returns True only when an email actually went out. A partner's client gets it from
    the partner (its sender and reply-to); to sends to an address that has no account yet (an invite)."""
    try:
        api_key = os.getenv("RESEND_API_KEY", "").strip()
        if not api_key:
            logger.warning(f"[notify] RESEND_API_KEY unset — skipped '{kind}' email")
            return False
        to = to or _user_email(user_id)
        if not to or _already_sent(key):
            return False
        partner = partner or (_partner_of(user_id) if user_id else None)
        if partner:
            subject, text, html_body = _in_partner_name(partner, subject, text, html_body)
        sender = partners.sender(partner) if partner else _FROM
        reply_to = reply_to or (partner["support_email"] if partner else _REPLY_TO)
        r = requests.post(_API_URL, timeout=15, headers={"Authorization": f"Bearer {api_key}"}, json={
            "from": sender, "to": [to], "reply_to": reply_to,
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


def _approve_url(pending_id: str) -> str:
    """This post's private approve page, or the queue if it has no link."""
    try:
        tok = (db.pending_posts.find_one({"id": pending_id}, {"approve_token": 1}) or {}).get("approve_token")
    except Exception:
        tok = None
    return f"{APP_URL}/approve/{tok}" if tok else _QUEUE_URL


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
    url = _approve_url(pending_id)
    ask = ""
    try:
        import notes
        q = notes.question_for(company)
        if not q["status"] and not held_reason:
            paras.append(f"<b>One question this week</b> (optional): {html.escape(q['text'])} Answer in a line or two "
                         "on the approve page, and Voyce builds an upcoming post around it.")
            ask = f"One question this week (optional): {q['text']} Answer on the approve page.\n\n"
    except Exception:
        pass
    # With daily posting on, tomorrow's post replaces this one (_save_pending), well before the 48 hours.
    expiry = ("If you don't approve it, it never goes out, and tomorrow's post replaces it, so stale news never goes out."
              if company.get("active") else "Posts you don't approve expire after 48 hours, so stale news never goes out.")
    text = (f"{html.unescape(intro)}\n\n{preview}\n\n"
            + (f"{src_text}\n\n" if src_text else "") + ask
            + f"Review and approve (no login needed): {url}\n\n{expiry}")
    body = _layout("Ready for your OK" if not held_reason else "Held for your review",
                   paras, button=("Review and approve", url), quote=preview, footer=expiry)
    return send(company.get("user_id", ""), f"ready:{pending_id}", "post_ready", subject, text, body)


REMIND_AFTER_HOURS = (10, 22)   # one nudge in this window, before tomorrow's post replaces it at about 24


def approval_reminders() -> int:
    """One nudge per held post, 10-22 hours after the email: a daily profile's next post replaces it
    at about 24 hours, and anything else expires at 48."""
    from autonomous import PENDING_TTL_HOURS
    now = datetime.now()
    newest = (now - timedelta(hours=REMIND_AFTER_HOURS[0])).isoformat()
    oldest = (now - timedelta(hours=REMIND_AFTER_HOURS[1])).isoformat()
    sent = 0
    try:
        items = list(db.pending_posts.find({"status": "pending", "created_at": {"$lt": newest, "$gt": oldest}},
                                           {"_id": 0, "id": 1, "user_id": 1, "company_id": 1, "company_name": 1,
                                            "post_type": 1, "post_text": 1, "created_at": 1}))
    except Exception:
        logger.exception("[notify] reminder sweep failed")
        return 0
    for p in items:
        name = p.get("company_name") or "your profile"
        daily = bool((db.companies.find_one({"id": p.get("company_id", "")}, {"_id": 0, "active": 1}) or {}).get("active"))
        left = timedelta(hours=24 if daily else PENDING_TTL_HOURS) - (now - datetime.fromisoformat(p["created_at"]))
        hours = max(1, int(left.total_seconds() // 3600))
        preview = (p.get("post_text") or "").strip()
        preview = preview[:400].rstrip() + ("…" if len(preview) > 400 else "")
        url = _approve_url(p["id"])
        kind = html.escape(p.get("post_type") or "post")
        if daily:
            subject = f"Your post for {name} is still waiting"
            intro = (f"Voyce's {kind} for {html.escape(name)} is still waiting for your OK. Tomorrow's post replaces "
                     f"it in about {hours} hours, so approve it before then if you want it on LinkedIn.")
        else:
            subject = f"Your post for {name} expires in {hours} hours"
            intro = (f"Voyce's {kind} for {html.escape(name)} is still waiting. "
                     f"If you don't approve it in the next {hours} hours, it won't go out.")
        text = f"{html.unescape(intro)}\n\n{preview}\n\nReview and approve (no login needed): {url}"
        body = _layout("Still waiting for your OK", [intro], button=("Review and approve", url), quote=preview,
                       footer="This is the only reminder for this post.")
        if send(p.get("user_id", ""), f"remind:{p['id']}", "approval_reminder", subject, text, body):
            sent += 1
    return sent


# Errors already written for people: LinkedIn's (linkedin.describe_api_failure) and our own.
_PLAIN_ERRORS = ("LinkedIn", "Carousel", "Image", "Person ID")


def _plain_reason(error: str) -> str:
    """Why a run failed, in words the customer can use. Raw API errors (a Claude 529 with its JSON,
    a timeout) are logged, not emailed."""
    reason = (error or "").strip()
    if reason.startswith(_PLAIN_ERRORS):
        return reason[:240] + ("…" if len(reason) > 240 else "")
    logger.warning(f"[notify] run failed: {reason[:500]}")
    low = reason.lower()
    if any(k in low for k in ("overloaded", "529", "429", "503", "rate limit", "quota", "resource_exhausted", "busy")):
        return "the AI that writes your posts was overloaded at that moment. Nothing was published."
    if "timed out" in low or "timeout" in low:
        return "writing it took too long and was stopped. Nothing was published."
    return "something went wrong on our side while writing it. Nothing was published, and we've logged the details."


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
    reason = _plain_reason(error)
    intro = (f"Voyce tried to write today's post for {html.escape(name)}, but it failed: "
             f"<em>{html.escape(reason)}</em>")
    button = "✎ Write now" if company.get("approval_mode") else "▶ Post now"
    nxt = (f"You can try again with <strong>{button}</strong> on the profile in Settings, "
           "or it will run again at the next scheduled time.")
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
    p = _partner_of(uid)
    if p:
        refill = partners.next_month_start()
        when = f"{refill.day} {refill.strftime('%b')}"
        intro = (f"You've used this month's {p['drafts_per_seat']} drafts, so {p['brand']} has paused your daily posts "
                 f"until {when}. Your profile and voice are all still saved.")
        nxt = f"They start again on {when} by themselves. Need more before then? Ask {p['company_short']}."
        url = app_url(p) + "/setup"
        return send(uid, f"paused:month:{uid}:{partners.month_key()}", "autopilot_paused", f"{p['brand']} is paused until {when}",
                    f"{intro}\n\n{nxt}\n\n{url}", _layout("Daily posts paused", [intro, nxt], button=(f"Open {p['brand']}", url),
                                                        partner=p))
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


# ── A partner's own emails (partner_panel.py) ─────────────────────────────────

def partner_invite(partner: dict, invite: dict, link: str, inviter: str = "") -> bool:
    """A partner's team invited someone to a seat. The link is theirs alone, for 14 days."""
    brand, firm = partner["brand"], partner["company_short"]
    first = (invite.get("name") or "").split(" ")[0]
    hello = f"Hi {html.escape(first)}," if first else "Hello,"
    intro = (f"{html.escape(inviter or firm)} has invited you to <strong>{html.escape(brand)}</strong>, "
             f"{html.escape(firm)}'s LinkedIn writing studio. It drafts posts in your own voice from your experience "
             "and this week's news in your field, and nothing goes out until you approve it.")
    nxt = (f"Create your account with this email address ({html.escape(invite['email'])}). "
           "The link is yours alone and works for 14 days.")
    text = f"{hello}\n\n{html.unescape(intro).replace('<strong>', '').replace('</strong>', '')}\n\n{html.unescape(nxt)}\n\n{link}"
    body = _layout(f"You're invited to {brand}", [hello, intro, nxt], button=("Accept the invite", link),
                   footer=f"{html.escape(partner['brand_full'])} · powered by Voyce", partner=partner)
    return send("", f"partner_invite:{invite['id']}", "partner_invite", f"{inviter or firm} invited you to {brand}",
                text, body, to=invite["email"], partner=partner)


def partner_reminder(partner: dict, user: dict, waiting: int, sent_by: str = "") -> bool:
    """A partner's team nudged a client about drafts waiting for their Approve. At most one a day."""
    brand, firm = partner["brand"], partner["company_short"]
    url = app_url(partner) + "/setup"
    n = f"{waiting} draft{'s' if waiting != 1 else ''}"
    intro = (f"You have {n} waiting in {html.escape(brand)}. Read {'them' if waiting != 1 else 'it'}, change anything "
             "you like, and tap Approve when it sounds like you." if waiting else
             f"A quick nudge from {html.escape(sent_by or firm)}: your next post in {html.escape(brand)} is a few minutes away.")
    text = f"{html.unescape(intro)}\n\n{url}"
    body = _layout(f"{n.capitalize()} waiting for you" if waiting else f"Your {brand} posts", [intro],
                   button=("Open " + brand, url), partner=partner)
    return send(user["id"], f"partner_remind:{user['id']}:{datetime.now().strftime('%Y-%m-%d')}", "partner_remind",
                f"{n.capitalize()} waiting in {brand}" if waiting else f"Your {brand} posts", text, body, partner=partner)


def partner_waitlist(partner: dict, entry: dict) -> bool:
    """Someone joined a partner's waiting list on its landing page: to the partner, with Reply-To set to them,
    so answering the email writes to the person who asked."""
    brand = partner["brand"].upper()
    rows = [("Name", entry["name"]), ("Title", entry["title"]), ("Company", entry["company"]),
            ("Email", entry["email"]), ("Phone", entry["phone"])]
    when = (datetime.fromisoformat(entry["at"]) + timedelta(hours=5, minutes=30)).strftime("%d %b %Y, %H:%M")
    intro = (f"{html.escape(entry['name'])} ({html.escape(entry['title'])}, {html.escape(entry['company'])}) "
             f"joined the {brand} waiting list on {partners.base_url(partner).split('://')[1]}.")
    table = "".join(f'<tr><td style="padding:4px 16px 4px 0;color:#7a8190;">{k}</td>'
                    f'<td style="padding:4px 0;color:#16181d;">{html.escape(v)}</td></tr>' for k, v in rows)
    body = _layout(f"New request for {brand}",
                   [intro, f'<table style="font-size:15px;border-collapse:collapse;">{table}</table>',
                    "Reply to this email to write to them directly."],
                   footer=f"Received {when} IST", partner=partner)
    text = (f"{html.unescape(intro)}\n\n" + "\n".join(f"{k}: {v}" for k, v in rows) +
            f"\n\nReply to this email to write to them directly.\nReceived {when} IST")
    return send("", f"partner_waitlist:{entry['id']}", "partner_waitlist",
                f"{brand} waiting list: {entry['name']}, {entry['company']}", text, body,
                to=partner["waitlist_to"], partner=partner, reply_to=entry["email"])


def partner_seat_request(partner: dict, seats: int, note: str, by: str) -> bool:
    """A partner asked Voyce for more seats: to Voyce's team (ADMIN_EMAILS)."""
    sent = False
    for admin in [e.strip() for e in os.getenv("ADMIN_EMAILS", "").split(",") if e.strip()]:
        intro = (f"{html.escape(by)} at {html.escape(partner['company'])} asked for "
                 f"<strong>{seats} more seat{'s' if seats != 1 else ''}</strong> on {html.escape(partner['brand'])}.")
        paras = [intro] + ([f"Their note: {html.escape(note)}"] if note else [])
        text = html.unescape(intro).replace("<strong>", "").replace("</strong>", "") + (f"\n\nTheir note: {note}" if note else "")
        sent |= send("", f"seat_request:{partner['id']}:{datetime.now().isoformat()[:16]}:{admin}", "seat_request",
                     f"{partner['brand']}: {seats} more seat{'s' if seats != 1 else ''} requested", text,
                     _layout("More seats requested", paras), to=admin)
    return sent
