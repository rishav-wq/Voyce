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
PRO_PRICE_AMOUNT_IN      = int(os.getenv("PRO_PRICE_AMOUNT_IN", "199900"))   # ₹1,999
REGION_IN, REGION_INTL   = "IN", "INTL"
INDIA_AUTOPILOT_PROFILES = 1
# Each India pass also includes a fixed number of generations (daily posting
# uses ~30 of them); the international pass is unlimited.
INDIA_PASS_GENERATIONS   = int(os.getenv("INDIA_PASS_GENERATIONS", "60"))
MAX_PROFILES             = 3   # per account, on every plan

_API_BASE = "https://api.razorpay.com/v1"


def is_configured() -> bool:
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


def region_for(country: str) -> str:
    """From Cloudflare's CF-IPCountry. Anything not clearly India — including no
    header at all (local dev, the onrender URL) — gets the international price."""
    return REGION_IN if (country or "").strip().upper() == "IN" else REGION_INTL


def price_for(region: str) -> int:
    return PRO_PRICE_AMOUNT_IN if region == REGION_IN else PRO_PRICE_AMOUNT


def get_config(region: str = REGION_INTL) -> dict:
    return {
        "configured": is_configured(),
        "key_id": RAZORPAY_KEY_ID,
        "amount": price_for(region),
        "currency": PRO_PRICE_CURRENCY,
        "region": region,
        "autopilot_profiles": INDIA_AUTOPILOT_PROFILES if region == REGION_IN else None,
        "pass_generations": INDIA_PASS_GENERATIONS if region == REGION_IN else None,
        "max_profiles": MAX_PROFILES,
    }


def create_order(user_id: str, email: str, region: str = REGION_INTL) -> dict:
    """Create a Razorpay order and record it so /payments/verify can
    confirm the order belongs to the paying user. The amount is set here, from
    the region the server worked out — never taken from the browser."""
    resp = httpx.post(
        f"{_API_BASE}/orders",
        auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
        json={
            "amount": price_for(region),
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
                                               "pass_gens_allowance": 1}) or {}
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
    if region == REGION_IN:
        # Count this pass's generations from here. Renewing early carries over
        # whatever the current India pass hadn't used, like the days do.
        used_total = int(user.get("gens_used", 0) or 0)
        carry = 0
        if still_active and user.get("plan_region") == REGION_IN:
            used_this_pass = used_total - int(user.get("pass_gens_start", used_total) or 0)
            carry = max(0, int(user.get("pass_gens_allowance", INDIA_PASS_GENERATIONS) or 0) - used_this_pass)
        fields.update({"pass_gens_start": used_total, "pass_gens_allowance": INDIA_PASS_GENERATIONS + carry})
    db.users.update_one({"id": user_id}, {"$set": fields})
    import analytics
    analytics.capture(user_id, "payment_succeeded", {"region": region, "source": source, "renewal": still_active})
