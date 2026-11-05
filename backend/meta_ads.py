"""Meta (Facebook and Instagram) ads measurement: the Meta Pixel in the browser and the Conversions API from
the server, so Meta can tell which ads bring people who sign up, write a first post and pay.

Config: META_PIXEL_ID and META_CAPI_TOKEN (Events Manager → the dataset → Settings), and META_TEST_EVENT_CODE
only while checking events in Events Manager → Test Events. With no pixel ID everything here is a no-op.

Only people in META_COUNTRIES are measured (default IN: the ads run in India). Anyone else never loads the
pixel, and no server event is sent for an account that signed up from elsewhere. A partner's visitors and
clients are never measured, and the pixel stays off Voyce's signed-in screens.

What Meta receives: page views on the public pages, a Voice Check run, and four server events (CompleteRegistration
for a new account, FirstPost, InitiateCheckout, Purchase), each with Meta's own browser cookies (_fbp, _fbc), the
IP address and browser, and the account's email and ID hashed with SHA-256. Never posts, profiles or LinkedIn data.

Every new account also keeps the campaign link that brought it (signup_source), whatever the country: Voyce's own
record of which ad or campaign works, readable without Meta.
"""
import contextvars
import hashlib
import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from urllib.parse import unquote

import requests

import db
import partners

GRAPH_VERSION = os.getenv("META_GRAPH_VERSION", "v24.0")
SOURCE_COOKIE = "v_src"   # the last campaign link that brought this browser here, set by the loader below
_SOURCE_KEYS = ("utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "fbclid", "landing", "at")
# Signed-in screens and one-time links: the pixel never loads there
_NO_PIXEL = r"^\/(tool|setup|onboarding|sales|approve|connect|partner)"


def _pixel_id() -> str:
    return os.getenv("META_PIXEL_ID", "").strip()


def _token() -> str:
    return os.getenv("META_CAPI_TOKEN", "").strip()


def _off() -> bool:
    # Test runs point Mongo at a dead local port; their events must never reach Meta (as in analytics.py).
    return not _pixel_id() or os.getenv("MONGO_URI", "").startswith("mongodb://127.0.0.1:1")


def measures(country: str) -> bool:
    countries = {c.strip().upper() for c in os.getenv("META_COUNTRIES", "IN").split(",") if c.strip()}
    return bool(_pixel_id()) and (country or "").strip().upper() in countries


# ── The browser ───────────────────────────────────────────────────────────────

# Meta's own loader snippet, verbatim from developers.facebook.com/docs/meta-pixel/get-started.
_PIXEL = r"""!function(f,b,e,v,n,t,s){if(f.fbq)return;n=f.fbq=function(){n.callMethod?n.callMethod.apply(n,arguments):n.queue.push(arguments)};if(!f._fbq)f._fbq=n;n.push=n;n.loaded=!0;n.version='2.0';n.queue=[];t=b.createElement(e);t.async=!0;t.src=v;s=b.getElementsByTagName(e)[0];s.parentNode.insertBefore(t,s)}(window,document,'script','https://connect.facebook.net/en_US/fbevents.js');"""

# Keeps the last campaign link (utm_* or Meta's fbclid) for 30 days, so the account made later knows where it
# came from. Only those fields, never anything else in the address.
_SOURCE = r"""
(function () {
  try {
    var q = new URLSearchParams(location.search), keep = {}, any = false;
    ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term", "fbclid"].forEach(function (k) {
      var v = q.get(k); if (v) { keep[k] = v.slice(0, 200); any = true; }
    });
    if (!any) return;
    keep.landing = location.pathname.slice(0, 100); keep.at = Date.now();
    document.cookie = "v_src=" + encodeURIComponent(JSON.stringify(keep)) + "; Max-Age=2592000; Path=/; SameSite=Lax"
      + (location.protocol === "https:" ? "; Secure" : "");
  } catch (e) {}
})();
"""


def loader_js(country: str) -> str:
    """Added to /analytics.js: the campaign-link cookie for every visitor, and the pixel with a PageView
    for a measured visitor on a public page. vMeta(event) records a custom event; a no-op without the pixel."""
    if not measures(country):
        return _SOURCE + "window.vMeta = function () {};\n"
    return _SOURCE + (
        "window.vMeta = function (ev, props) { try { if (window.fbq) fbq('trackCustom', ev, props || {}); } catch (e) {} };\n"
        f"(function () {{ if (/{_NO_PIXEL}/.test(location.pathname)) return;\n"
        + _PIXEL + f"\nfbq('init', {json.dumps(_pixel_id())});\nfbq('track', 'PageView');\n}})();\n")


# ── The request in hand ───────────────────────────────────────────────────────
# Set for every request (main._visitor_context): what a server event needs from the browser that made it.
_visitor: contextvars.ContextVar = contextvars.ContextVar("meta_visitor", default=None)


def set_visitor(request):
    import ratelimit
    c = request.cookies
    return _visitor.set({
        "ip": ratelimit.visitor_ip(request)[:64],
        "ua": (request.headers.get("user-agent") or "")[:400],
        "fbp": (c.get("_fbp") or "")[:200],
        "fbc": (c.get("_fbc") or "")[:300],
        "src": (c.get(SOURCE_COOKIE) or "")[:1500],
        "country": (request.headers.get("cf-ipcountry") or "").strip().upper()[:2],
        "page": (request.headers.get("referer") or "")[:500],   # the page whose script made this request
    })


def reset_visitor(token) -> None:
    _visitor.reset(token)


def _source(raw: str) -> dict:
    try:
        d = json.loads(unquote(raw)) if raw else {}
        return {k: str(d[k])[:200] for k in _SOURCE_KEYS if d.get(k) not in (None, "")} if isinstance(d, dict) else {}
    except (ValueError, TypeError):
        return {}


def _visit(v: dict, src: dict | None = None) -> dict:
    """The browser details an event carries. Meta's click cookie (_fbc) comes from the pixel; without it (an ad
    blocker, a slow page) it is rebuilt from the fbclid the link carried, in Meta's own format."""
    fbc = v.get("fbc", "")
    src = src if src is not None else _source(v.get("src", ""))
    if not fbc and src.get("fbclid"):
        fbc = f"fb.1.{src.get('at') or int(time.time() * 1000)}.{src['fbclid']}"
    return {"ip": v.get("ip", ""), "ua": v.get("ua", ""), "fbp": v.get("fbp", ""), "fbc": fbc,
            "page": v.get("page", ""), "at": datetime.now(timezone.utc).isoformat()}


def _fresh(user: dict) -> dict:
    """The stored visit, updated with this request's browser when there is one: a later visit can bring a
    newer ad click. Saved, so a payment confirmed by Razorpay's server still has the buyer's browser."""
    stored = user.get("meta_visit") or {}
    v = _visitor.get()
    if not v or not v.get("ua"):
        return stored
    now = _visit(v)
    merged = {**stored, **{k: val for k, val in now.items() if val}}
    try:
        db.users.update_one({"id": user["id"]}, {"$set": {"meta_visit": merged}})
    except Exception:
        logging.exception("[meta] could not keep the visit")
    return merged


# ── Events ────────────────────────────────────────────────────────────────────

def _sha(value: str) -> str:
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()


def _send(name: str, event_id: str, user: dict, visit: dict, custom: dict | None = None) -> None:
    """Fire-and-forget Conversions API event. Never raises, never blocks the request."""
    if _off() or not _token() or not visit.get("ua"):
        return   # Meta rejects a website event without the browser it came from
    data = {"client_ip_address": visit.get("ip") or None, "client_user_agent": visit["ua"],
            "external_id": [_sha(user["id"])], "fbp": visit.get("fbp") or None, "fbc": visit.get("fbc") or None}
    if user.get("email"):
        data["em"] = [_sha(user["email"])]
    if user.get("signup_country"):
        data["country"] = [_sha(user["signup_country"])]
    event = {"event_name": name, "event_time": int(time.time()), "event_id": event_id, "action_source": "website",
             "event_source_url": visit.get("page") or "https://voyce.co.in/",
             "user_data": {k: v for k, v in data.items() if v}}
    if custom:
        event["custom_data"] = custom
    payload = {"data": [event]}
    test_code = os.getenv("META_TEST_EVENT_CODE", "").strip()
    if test_code:
        payload["test_event_code"] = test_code

    def _post():
        try:
            r = requests.post(f"https://graph.facebook.com/{GRAPH_VERSION}/{_pixel_id()}/events",
                              params={"access_token": _token()}, json=payload, timeout=8)
            if r.ok:
                logging.info(f"[meta] sent {name} ({event_id})")
            else:
                logging.error(f"[meta] Meta rejected {name}: {r.status_code} {r.text[:300]}")
        except Exception:
            logging.exception(f"[meta] {name} failed")

    threading.Thread(target=_post, daemon=True).start()


def _measured_user(user_id: str) -> dict | None:
    """The account if Meta may hear about it: signed up in a measured country, not a partner's client."""
    if _off() or partners.current():
        return None
    u = db.users.find_one({"id": user_id}, {"_id": 0, "id": 1, "email": 1, "partner_id": 1, "signup_country": 1,
                                            "meta_visit": 1, "meta_first_post": 1})
    if not u or u.get("partner_id") or not measures(u.get("signup_country", "")):
        return None
    return u


def signed_up(user: dict) -> None:
    """A new Voyce account (auth.get_user_by_token, at first sign-in, in the browser that signed up)."""
    try:
        if user.get("partner_id") or partners.current():
            return
        v = _visitor.get() or {}
        src = _source(v.get("src", ""))
        fields = {"signup_country": v.get("country", ""), "signup_source": src}
        measured = measures(v.get("country", ""))
        if measured:
            fields["meta_visit"] = _visit(v, src)
        db.users.update_one({"id": user["id"]}, {"$set": fields})
        if measured and not _off():
            _send("CompleteRegistration", f"reg_{user['id']}", {**user, **fields}, fields["meta_visit"])
    except Exception:
        logging.exception("[meta] sign-up event failed")


def first_post(user_id: str) -> None:
    """Their first post written (/preview from onboarding or /generate on Write). Once per account."""
    try:
        u = _measured_user(user_id)
        if not u or u.get("meta_first_post"):
            return
        claimed = db.users.update_one({"id": user_id, "meta_first_post": {"$exists": False}},
                                      {"$set": {"meta_first_post": datetime.now(timezone.utc).isoformat()}})
        if claimed.modified_count:
            _send("FirstPost", f"first_{user_id}", u, _fresh(u))
    except Exception:
        logging.exception("[meta] first-post event failed")


def checkout(user_id: str, order: dict) -> None:
    """The Razorpay checkout opened (/payments/create-order)."""
    try:
        u = _measured_user(user_id)
        if u:
            _send("InitiateCheckout", f"checkout_{order.get('id', '')}", u, _fresh(u),
                  {"value": round(int(order.get("amount", 0)) / 100, 2), "currency": order.get("currency", "INR")})
    except Exception:
        logging.exception("[meta] checkout event failed")


def purchase(user_id: str, order_id: str, payment_id: str, amount: int, currency: str) -> None:
    """A pass paid for (payments.activate_pro). Uses the browser stored at sign-up or checkout, never this
    request's: Razorpay's webhook confirms many payments, and its server is not the buyer."""
    try:
        u = _measured_user(user_id)
        if u and u.get("meta_visit"):
            _send("Purchase", f"pay_{payment_id or order_id}", u, u["meta_visit"],
                  {"value": round(int(amount or 0) / 100, 2), "currency": currency or "INR", "order_id": order_id})
    except Exception:
        logging.exception("[meta] purchase event failed")
