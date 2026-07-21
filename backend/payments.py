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

# Amount in the currency's smallest unit (paise for INR). Default ₹4,199 ≈ $49.
PRO_PRICE_AMOUNT   = int(os.getenv("PRO_PRICE_AMOUNT", "419900"))
PRO_PRICE_CURRENCY = os.getenv("PRO_PRICE_CURRENCY", "INR")
PRO_DURATION_DAYS  = int(os.getenv("PRO_DURATION_DAYS", "31"))

_API_BASE = "https://api.razorpay.com/v1"


def is_configured() -> bool:
    return bool(RAZORPAY_KEY_ID and RAZORPAY_KEY_SECRET)


def get_config() -> dict:
    return {
        "configured": is_configured(),
        "key_id": RAZORPAY_KEY_ID,
        "amount": PRO_PRICE_AMOUNT,
        "currency": PRO_PRICE_CURRENCY,
    }


def create_order(user_id: str, email: str) -> dict:
    """Create a Razorpay order and record it so /payments/verify can
    confirm the order belongs to the paying user."""
    resp = httpx.post(
        f"{_API_BASE}/orders",
        auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET),
        json={
            "amount": PRO_PRICE_AMOUNT,
            "currency": PRO_PRICE_CURRENCY,
            "receipt": f"voyce_pro_{user_id[:24]}",
            "notes": {"user_id": user_id, "email": email, "product": "voyce_pro"},
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
    expires_at = (datetime.now() + timedelta(days=PRO_DURATION_DAYS)).isoformat()
    db.payments.update_one(
        {"order_id": order_id},
        {"$set": {
            "status":     "paid",
            "payment_id": payment_id,
            "source":     source,
            "paid_at":    datetime.now().isoformat(),
        }},
    )
    db.users.update_one(
        {"id": user_id},
        {"$set": {"plan": "pro", "plan_expires_at": expires_at}},
    )
