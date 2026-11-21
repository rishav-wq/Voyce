import hashlib
import hmac
import os
from datetime import datetime, timedelta

import httpx
from dotenv import load_dotenv

import db

load_dotenv()

RAZORPAY_KEY_ID        = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET    = os.getenv("RAZORPAY_KEY_SECRET", "")
RAZORPAY_WEBHOOK_SECRET = os.getenv("RAZORPAY_WEBHOOK_SECRET", "")

# Amount in the currency's smallest unit (paise for INR). Default ₹4,699 ≈ $49.
PRO_PRICE_AMOUNT   = int(os.getenv("PRO_PRICE_AMOUNT", "469900"))
PRO_PRICE_CURRENCY = os.getenv("PRO_PRICE_CURRENCY", "INR")
PRO_DURATION_DAYS  = int(os.getenv("PRO_DURATION_DAYS", "31"))

# Location-based pricing. ₹4,699 is right for the $49 international buyer and
# too much for India, so visitors in India pay a founding India price. Their pass
# covers one profile on daily posting: AI cost grows with daily profiles, and at
# this price several of them would eat the margin.
PRO_PRICE_AMOUNT_IN      = int(os.getenv("PRO_PRICE_AMOUNT_IN", "299900"))   # ₹2,999 (was ₹1,999 until 16 Nov 2026)
REGION_IN, REGION_INTL   = "IN", "INTL"
INDIA_AUTOPILOT_PROFILES = 1
# Each India pass also includes a fixed number of generations (daily posting
# uses ~30 of them); the international pass is unlimited.
INDIA_PASS_GENERATIONS   = int(os.getenv("INDIA_PASS_GENERATIONS", "60"))
# AI pictures (drawn by the image model: idea pictures, photos) cost ~₹8 each to draw and
# check, on top of the writing, against next to nothing for the cards we draw, so each pass
# includes a number of them inside its generations. Free accounts get one to see what they are.
INDIA_PASS_AI_PICTURES   = int(os.getenv("INDIA_PASS_AI_PICTURES", "10"))
INTL_PASS_AI_PICTURES    = int(os.getenv("INTL_PASS_AI_PICTURES", "40"))
FREE_AI_PICTURES         = int(os.getenv("FREE_AI_PICTURES", "1"))
MAX_PROFILES             = 3   # per account, on every plan

# Pro is bought on autopay (21 Nov 2026): a Razorpay subscription that charges the same price every
# month until the user turns it off. UPI Autopay or a card mandate in India, a card abroad. Each
# charge is one month of Pro through activate_pro, exactly like a pass. 60 monthly charges at most
# (a subscription needs an end); after five years it simply completes.
SUB_TOTAL_COUNT = int(os.getenv("SUB_TOTAL_COUNT", "60"))
SUB_LIVE = ("created", "authenticated", "active", "pending")   # still able to charge
SUB_ON   = ("authenticated", "active", "pending")              # autopay is set up

_API_BASE = "https://api.razorpay.com/v1"


def is_configured() -> bool:
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


def region_for(country: str) -> str:
    """From Cloudflare's CF-IPCountry. Anything not clearly India — including no
    header at all (local dev, the onrender URL) — gets the international price."""
    return REGION_IN if (country or "").strip().upper() == "IN" else REGION_INTL


def price_for(region: str, user: dict | None = None) -> int:
    # A price kept for one account (users.price_in, in paise): a customer who bought before a price rise
    # keeps their price, as Sam keeps ₹1,999 after the 16 Nov 2026 rise to ₹2,999. India only.
    if region == REGION_IN and user and user.get("price_in"):
        return int(user["price_in"])
    return PRO_PRICE_AMOUNT_IN if region == REGION_IN else PRO_PRICE_AMOUNT


def get_config(region: str = REGION_INTL, user: dict | None = None) -> dict:
    return {
        "configured": is_configured(),
        "key_id": RAZORPAY_KEY_ID,
        "amount": price_for(region, user),
        "currency": PRO_PRICE_CURRENCY,
        "region": region,
        "autopilot_profiles": INDIA_AUTOPILOT_PROFILES if region == REGION_IN else None,
        "pass_generations": INDIA_PASS_GENERATIONS if region == REGION_IN else None,
        "pass_ai_pictures": INDIA_PASS_AI_PICTURES if region == REGION_IN else INTL_PASS_AI_PICTURES,
        "free_ai_pictures": FREE_AI_PICTURES,
        "max_profiles": MAX_PROFILES,
    }


def create_order(user_id: str, email: str, region: str = REGION_INTL) -> dict:
    """Create a Razorpay order and record it so /payments/verify can
    confirm the order belongs to the paying user. The amount is set here, from
    the region the server worked out — never taken from the browser."""
    user = db.users.find_one({"id": user_id}, {"_id": 0, "price_in": 1})
    resp = httpx.post(
        f"{_API_BASE}/orders",
        auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
        json={
            "amount": price_for(region, user),
            "currency": PRO_PRICE_CURRENCY,
            "receipt": f"voyce_pro_{user_id[:24]}",
            "notes": {"user_id": user_id, "email": email, "product": "voyce_pro", "region": region},
        },
        timeout=15,
    )
    resp.raise_for_status()
    order = resp.json()
    db.payments.insert_one({
        "order_id":   order["id"],
        "user_id":    user_id,
        "email":      email,
        "amount":     order["amount"],
        "currency":   order["currency"],
        "region":     region,
        "status":     "created",
        "created_at": datetime.now().isoformat(),
    })
    return order


def verify_payment_signature(order_id: str, payment_id: str, signature: str) -> bool:
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(),
        f"{order_id}|{payment_id}".encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def verify_webhook_signature(body: bytes, signature: str) -> bool:
    if not RAZORPAY_WEBHOOK_SECRET:
        return False
    expected = hmac.new(RAZORPAY_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def get_order_record(order_id: str) -> dict | None:
    return db.payments.find_one({"order_id": order_id}, {"_id": 0})


def pending_orders(user_id: str) -> list[dict]:
    return list(db.payments.find({"user_id": user_id, "status": "created"}, {"_id": 0}))


def payment_history(user_id: str) -> list[dict]:
    return list(
        db.payments.find({"user_id": user_id, "status": "paid"}, {"_id": 0})
        .sort("paid_at", -1)
        .limit(24)
    )


def find_captured_payment(order_id: str) -> dict | None:
    """Ask Razorpay directly whether an order was paid — recovery path for
    users whose browser died between payment and verification."""
    resp = httpx.get(
        f"{_API_BASE}/orders/{order_id}/payments",
        auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
        timeout=15,
    )
    resp.raise_for_status()
    for p in resp.json().get("items", []):
        if p.get("status") == "captured":
            return p
    return None


def activate_pro(user_id: str, order_id: str, payment_id: str, source: str = "checkout"):
    """Mark the payment captured and grant Pro for PRO_DURATION_DAYS.
    Idempotent — a webhook firing after checkout verification is a no-op."""
    record = db.payments.find_one({"order_id": order_id})
    if record and record.get("status") == "paid":
        return
    # Renewing early stacks on the time already paid for, instead of throwing
    # the remaining days away (the dashboard invites renewal 5 days out).
    start = datetime.now()
    user = db.users.find_one({"id": user_id}, {"plan": 1, "plan_expires_at": 1, "plan_region": 1,
                                               "gens_used": 1, "pass_gens_start": 1,
                                               "pass_gens_allowance": 1, "ai_pics_used": 1,
                                               "pass_ai_start": 1, "pass_ai_allowance": 1}) or {}
    still_active = False
    if user.get("plan") == "pro" and user.get("plan_expires_at"):
        try:
            still_active = datetime.fromisoformat(user["plan_expires_at"]) > start
            start = max(start, datetime.fromisoformat(user["plan_expires_at"]))
        except (TypeError, ValueError):
            pass
    expires_at = (start + timedelta(days=PRO_DURATION_DAYS)).isoformat()
    db.payments.update_one(
        {"order_id": order_id},
        {"$set": {
            "status":     "paid",
            "payment_id": payment_id,
            "source":     source,
            "paid_at":    datetime.now().isoformat(),
        }},
    )
    # The plan follows the latest purchase: an India pass includes one profile on
    # daily posting. Orders from before regional pricing count as international.
    region = (record or {}).get("region", REGION_INTL)
    fields = {"plan": "pro", "plan_expires_at": expires_at, "plan_region": region}
    # A month bought by hand carries over what the last one hadn't used, like the days do. An autopay
    # renewal starts the month fresh instead: carried forever, allowances would pile up month on month.
    carry_ok = still_active and source != "autopay"
    if region == REGION_IN:
        # Count this pass's generations from here.
        used_total = int(user.get("gens_used", 0) or 0)
        carry = 0
        if carry_ok and user.get("plan_region") == REGION_IN:
            used_this_pass = used_total - int(user.get("pass_gens_start", used_total) or 0)
            carry = max(0, int(user.get("pass_gens_allowance", INDIA_PASS_GENERATIONS) or 0) - used_this_pass)
        fields.update({"pass_gens_start": used_total, "pass_gens_allowance": INDIA_PASS_GENERATIONS + carry})
    # AI pictures are counted per pass on both regions, unused ones carried over the same way
    pics_total = int(user.get("ai_pics_used", 0) or 0)
    pics_carry = 0
    if carry_ok and user.get("plan_region") == region and user.get("pass_ai_allowance") is not None:
        pics_used = pics_total - int(user.get("pass_ai_start", pics_total) or 0)
        pics_carry = max(0, int(user["pass_ai_allowance"]) - pics_used)
    pics = INDIA_PASS_AI_PICTURES if region == REGION_IN else INTL_PASS_AI_PICTURES
    fields.update({"pass_ai_start": pics_total, "pass_ai_allowance": pics + pics_carry})
    db.users.update_one({"id": user_id}, {"$set": fields})
    import analytics
    import meta_ads
    analytics.capture(user_id, "payment_succeeded", {"region": region, "source": source, "renewal": still_active})
    if source != "autopay":   # a monthly renewal isn't a new purchase for the ads to learn from
        meta_ads.purchase(user_id, order_id, payment_id, (record or {}).get("amount", price_for(region)),
                          (record or {}).get("currency", PRO_PRICE_CURRENCY))


# ── Autopay (Razorpay Subscriptions) ──────────────────────────────────────────
def _auth():
    return (RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)


def _key_mode() -> str:
    return "live" if RAZORPAY_KEY_ID.startswith("rzp_live") else "test"


def plan_for(amount: int, region: str) -> str:
    """The Razorpay monthly plan for this price, made the first time it's needed and kept. One per
    price (an account with its own India price gets its own plan) and per key mode, because test and
    live keys see different plans."""
    mode = _key_mode()
    rec = db.rzp_plans.find_one({"amount": amount, "currency": PRO_PRICE_CURRENCY, "mode": mode}, {"_id": 0})
    if rec:
        return rec["plan_id"]
    label = "India" if region == REGION_IN else "International"
    resp = httpx.post(f"{_API_BASE}/plans", auth=_auth(), timeout=15, json={
        "period": "monthly", "interval": 1,
        "item": {"name": f"Voyce Pro ({label})", "amount": amount, "currency": PRO_PRICE_CURRENCY,
                 "description": "Voyce Pro, charged monthly until you turn autopay off"},
        "notes": {"product": "voyce_pro", "region": region},
    })
    resp.raise_for_status()
    plan = resp.json()
    db.rzp_plans.insert_one({"plan_id": plan["id"], "amount": amount, "currency": PRO_PRICE_CURRENCY,
                             "mode": mode, "region": region, "created_at": datetime.now().isoformat()})
    return plan["id"]


def current_subscription(user_id: str, statuses: tuple = SUB_ON) -> dict | None:
    """The user's newest subscription in one of these states."""
    return db.subscriptions.find_one({"user_id": user_id, "status": {"$in": list(statuses)}}, {"_id": 0},
                                     sort=[("created_at", -1)])


def get_subscription_record(subscription_id: str) -> dict | None:
    return db.subscriptions.find_one({"subscription_id": subscription_id}, {"_id": 0})


def create_subscription(user_id: str, email: str, region: str = REGION_INTL) -> dict:
    """Start autopay: a subscription on the monthly plan for this user's price. The first month is
    charged when they approve the mandate at checkout. The amount comes from the server's region,
    never from the browser."""
    if current_subscription(user_id):
        raise ValueError("AUTOPAY_ON")
    user = db.users.find_one({"id": user_id}, {"_id": 0, "price_in": 1})
    amount = price_for(region, user)
    plan_id = plan_for(amount, region)
    resp = httpx.post(f"{_API_BASE}/subscriptions", auth=_auth(), timeout=15, json={
        "plan_id": plan_id, "total_count": SUB_TOTAL_COUNT, "quantity": 1, "customer_notify": 1,
        "notes": {"user_id": user_id, "email": email, "product": "voyce_pro", "region": region},
    })
    resp.raise_for_status()
    sub = resp.json()
    db.subscriptions.insert_one({
        "subscription_id": sub["id"], "user_id": user_id, "email": email, "plan_id": plan_id,
        "amount": amount, "currency": PRO_PRICE_CURRENCY, "region": region,
        "status": sub.get("status", "created"), "created_at": datetime.now().isoformat(),
    })
    return {**sub, "amount": amount, "currency": PRO_PRICE_CURRENCY, "region": region}


def verify_subscription_signature(subscription_id: str, payment_id: str, signature: str) -> bool:
    expected = hmac.new(RAZORPAY_KEY_SECRET.encode(), f"{payment_id}|{subscription_id}".encode(),
                        hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def record_subscription_charge(sub: dict, payment_id: str, amount: int | None = None,
                               currency: str | None = None, source: str = "checkout"):
    """One charge on a subscription (the first at checkout, then one a month), kept as a payment and
    turned into a month of Pro. Keyed by the payment, so checkout and the webhook can both report it."""
    key = "subpay_" + payment_id
    db.payments.update_one({"order_id": key}, {"$setOnInsert": {
        "order_id": key, "payment_id": payment_id, "subscription_id": sub["subscription_id"],
        "user_id": sub["user_id"], "email": sub.get("email", ""), "amount": amount or sub.get("amount"),
        "currency": currency or sub.get("currency", PRO_PRICE_CURRENCY), "region": sub.get("region", REGION_INTL),
        "status": "created", "autopay": True, "created_at": datetime.now().isoformat(),
    }}, upsert=True)
    activate_pro(sub["user_id"], key, payment_id, source=source)


def update_subscription(subscription_id: str, entity: dict | None = None, **extra):
    """Store what Razorpay says about a subscription now (status, next charge, charges so far)."""
    fields = dict(extra)
    if entity:
        for k in ("status", "current_start", "current_end", "charge_at", "paid_count", "remaining_count", "ended_at"):
            if entity.get(k) is not None:
                fields[k] = entity[k]
    if fields:
        fields["updated_at"] = datetime.now().isoformat()
        db.subscriptions.update_one({"subscription_id": subscription_id}, {"$set": fields})


def handle_subscription_event(event: dict) -> bool:
    """A subscription.* webhook: keep our copy current, and on subscription.charged give the month of
    Pro (activate_pro ignores a charge it has already counted)."""
    payload = event.get("payload") or {}
    entity = ((payload.get("subscription") or {}).get("entity")) or {}
    rec = get_subscription_record(entity.get("id", ""))
    if not rec:
        return False
    update_subscription(rec["subscription_id"], entity)
    if event.get("event") == "subscription.charged":
        pay = ((payload.get("payment") or {}).get("entity")) or {}
        if pay.get("id"):
            renewal = int(entity.get("paid_count") or 0) > 1
            record_subscription_charge(rec, pay["id"], pay.get("amount"), pay.get("currency"),
                                       source="autopay" if renewal else "webhook")
    return True


def fetch_subscription(subscription_id: str) -> dict:
    resp = httpx.get(f"{_API_BASE}/subscriptions/{subscription_id}", auth=_auth(), timeout=15)
    resp.raise_for_status()
    return resp.json()


def restore_subscriptions(user_id: str) -> bool:
    """Recovery for a checkout whose browser closed before we heard back: ask Razorpay about this
    user's subscriptions and count any paid charge we missed."""
    restored = False
    for rec in db.subscriptions.find({"user_id": user_id, "status": {"$in": list(SUB_LIVE)}}, {"_id": 0}):
        sub = fetch_subscription(rec["subscription_id"])
        update_subscription(rec["subscription_id"], sub)
        if int(sub.get("paid_count") or 0) < 1:
            continue
        resp = httpx.get(f"{_API_BASE}/invoices", auth=_auth(), timeout=15,
                         params={"subscription_id": rec["subscription_id"], "count": 50})
        resp.raise_for_status()
        for inv in resp.json().get("items", []):
            pid = inv.get("payment_id")
            if pid and inv.get("status") == "paid" and not db.payments.find_one({"order_id": "subpay_" + pid, "status": "paid"}):
                record_subscription_charge(rec, pid, inv.get("amount"), inv.get("currency"), source="restore")
                restored = True
    return restored


def cancel_subscription(user_id: str) -> dict:
    """Turn autopay off. Pro runs to the end of the month already paid for and no further charge is
    made. A subscription that never got going is cancelled straight away."""
    rec = current_subscription(user_id, SUB_LIVE)
    if not rec:
        raise ValueError("NO_AUTOPAY")
    at_cycle_end = 1 if rec.get("status") == "active" else 0
    resp = httpx.post(f"{_API_BASE}/subscriptions/{rec['subscription_id']}/cancel", auth=_auth(), timeout=15,
                      json={"cancel_at_cycle_end": at_cycle_end})
    if resp.status_code >= 400 and at_cycle_end:   # e.g. Razorpay won't wait for this one: stop it now
        resp = httpx.post(f"{_API_BASE}/subscriptions/{rec['subscription_id']}/cancel", auth=_auth(), timeout=15,
                          json={"cancel_at_cycle_end": 0})
    resp.raise_for_status()
    update_subscription(rec["subscription_id"], resp.json(), cancel_requested_at=datetime.now().isoformat())
    return get_subscription_record(rec["subscription_id"]) or {}


def autopay_info(user_id: str) -> dict | None:
    """What the dashboard shows about autopay: on (and the next charge date), or turned off."""
    rec = db.subscriptions.find_one({"user_id": user_id, "status": {"$in": list(SUB_ON) + ["halted", "cancelled"]}},
                                    {"_id": 0}, sort=[("created_at", -1)])
    if not rec:
        return None
    on = rec.get("status") in SUB_ON and not rec.get("cancel_requested_at")
    nxt = rec.get("charge_at") or rec.get("current_end")
    return {"on": on, "status": rec.get("status"), "amount": rec.get("amount"), "currency": rec.get("currency"),
            "next_charge_at": datetime.fromtimestamp(nxt).isoformat() if (on and nxt) else None}
