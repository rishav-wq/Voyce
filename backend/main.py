import os
import re
import secrets
import logging
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Form, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Load backend/.env explicitly BEFORE importing local modules, so config is correct
# no matter which directory uvicorn is launched from. Running from the repo root
# would otherwise pick up the root .env (different key names, missing backend-only
# settings) and cause every authenticated request to 401. load_dotenv does not
# override real host env vars, so production (Render) is unaffected.
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

from processor import process_input
from generator import generate_content
from company import save_company, get_company, list_companies, delete_company, toggle_company, update_company, save_linkedin_data, set_scheduled_type
from autonomous import run_for_company, get_post_log, save_post_log
from linkedin_data import parse_linkedin_upload, parse_pasted_posts, parse_post_screenshots
import linkedin as li
import auth as auth_module
import payments
import ratelimit
import db

logging.basicConfig(level=logging.INFO)
app = FastAPI()

# Set ALLOWED_ORIGINS=https://yourdomain.com (comma-separated) in production
_allowed_origins = [o.strip() for o in os.getenv("ALLOWED_ORIGINS", "*").split(",") if o.strip()]
if _allowed_origins == ["*"]:
    logging.warning(
        "CORS is open to ALL origins because ALLOWED_ORIGINS is unset. "
        "Set ALLOWED_ORIGINS to your real domain(s) before serving production traffic."
    )
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allowed_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=frontend_path), name="static")

# ── Scheduler ────────────────────────────────────────────────────────────────
scheduler = BackgroundScheduler()
scheduler.start()


def _as_naive_local(dt: datetime) -> datetime:
    """Normalize an (optionally tz-aware) datetime to naive local time."""
    if dt.tzinfo:
        return dt.astimezone().replace(tzinfo=None)
    return dt


def _require_user(x_token: str = Header(None)):
    user = auth_module.get_user_by_token(x_token or "")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def _check_gen_limit(user: dict):
    info = auth_module.get_gen_info(user["id"])
    if info["limit"] != -1 and info["used"] >= info["limit"]:
        raise HTTPException(
            status_code=402,
            detail=f"LIMIT_REACHED"
        )


def _rate_limit(key: str, limit: int, window: float = 60.0):
    """Coarse per-key throttle for cost-incurring / abuse-prone endpoints.
    Raises 429 when exceeded. Complements _check_gen_limit (which caps free
    users by total gens but leaves Pro users and non-gen LLM calls unbounded)."""
    if not ratelimit.allow(key, limit, window):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait a minute and try again.",
        )


def _client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _is_pro(user: dict) -> bool:
    return auth_module.get_gen_info(user["id"])["limit"] == -1


def _require_pro(user: dict, feature: str):
    if not _is_pro(user):
        raise HTTPException(status_code=403, detail=f"PRO_REQUIRED:{feature}")


def _friendly_generation_error(exc: Exception) -> str:
    # Log the real error+traceback to the server console so failures are diagnosable
    # (the user only ever sees the friendly message below).
    import traceback
    print(f"[generation error] {type(exc).__name__}: {exc}", flush=True)
    traceback.print_exc()
    msg = str(exc)
    low = msg.lower()
    if "rate limit" in low or "rate_limit" in low or "quota" in low or "too many requests" in low:
        return "AI generation is temporarily rate-limited. Please try again in a few minutes."
    if "api key" in low or "authentication" in low or "unauthorized" in low:
        return "AI generation is temporarily unavailable. Please contact support if this keeps happening."
    if "timeout" in low or "timed out" in low:
        return "AI generation took too long. Please try again with shorter content."
    return "AI generation is temporarily unavailable. Please try again shortly."


def _friendly_fetch_error(exc: Exception, input_type: str) -> str:
    if input_type == "url":
        return "Could not read that URL. Try pasting the article text instead."
    if input_type == "youtube":
        return "Could not read that YouTube transcript. Try another video or paste the transcript text."
    return "Could not read that content. Please try again."


def _resolve_profile(user_id: str, profile_id: str = ""):
    """The profile to write as: the explicit valid choice if given, else the first one."""
    profiles = list_companies(user_id)
    if profile_id:
        for p in profiles:
            if p.get("id") == profile_id:
                return p
    return profiles[0] if profiles else None


def _with_profile_context(profile: dict | None, raw_text: str) -> str:
    if not profile:
        return raw_text
    context = [
        "Saved profile context for tone and relevance:",
        f"Name: {profile.get('name', '')}",
        f"Profile type: {profile.get('profile_type', 'company')}",
        f"Industry: {profile.get('industry', '')}",
        f"Tone: {profile.get('tone', '')}",
    ]
    if profile.get("designation"):
        context.append(f"Designation: {profile.get('designation')}")
    if profile.get("analysis", {}).get("description"):
        context.append(f"Background: {profile['analysis']['description']}")
    return "\n".join(context) + "\n\nContent to repurpose:\n" + raw_text


def _run_company_by_id(company_id: str):
    """Fetch fresh company data at job fire time, then run."""
    company = get_company(company_id)
    if company and company.get("active"):
        run_for_company(company)


def _setup_company_cron(company: dict):
    job_id = f"auto_{company['id']}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    hour, minute = company["post_time"].split(":")
    scheduler.add_job(
        _run_company_by_id,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), timezone="Asia/Kolkata"),
        args=[company["id"]],
        id=job_id,
        replace_existing=True,
    )


def _refresh_all_crons():
    for company in list_companies():
        if company.get("active", True):
            _setup_company_cron(company)


@app.on_event("startup")
def startup():
    _refresh_all_crons()
    _restore_scheduled_jobs()
    # Fire a catch-up sweep shortly after boot (off the startup thread, so the
    # health check responds immediately). This covers daily posts missed while
    # the instance was down — e.g. a redeploy or free-tier recycle landing on a
    # profile's post-time, which the in-memory cron would otherwise skip since it
    # only re-registers the NEXT slot on boot.
    scheduler.add_job(
        _catch_up_missed_posts,
        trigger="date",
        run_date=datetime.now() + timedelta(seconds=15),
        id="catchup_startup",
        replace_existing=True,
    )


def _do_scheduled_post(text: str, job_id: str, dry_run: bool = False, user_id: str = ""):
    try:
        if dry_run:
            print(f"\n[DRY RUN] Scheduled post fired:\n{text}\n")
        else:
            li.post_to_linkedin(user_id, text)
        db.scheduled.update_one({"id": job_id}, {"$set": {"status": "dry_run_fired" if dry_run else "posted"}})
    except Exception as e:
        db.scheduled.update_one({"id": job_id}, {"$set": {"status": f"failed: {str(e)}"}})


def _restore_scheduled_jobs():
    """Re-register pending one-off posts after a server restart."""
    now = datetime.now()
    for entry in db.scheduled.find({"status": {"$regex": "^scheduled"}}):
        try:
            run_at = _as_naive_local(datetime.fromisoformat(entry["scheduled_at"]))
        except Exception:
            continue
        if run_at <= now:
            db.scheduled.update_one({"id": entry["id"]}, {"$set": {"status": "missed (server was down)"}})
            continue
        scheduler.add_job(
            _do_scheduled_post,
            trigger="date",
            run_date=run_at,
            args=[entry.get("text", ""), entry["id"], "dry run" in entry.get("status", ""), entry.get("user_id", "")],
            id=entry["id"],
            replace_existing=True,
        )


# ── Startup catch-up for missed daily posts ────────────────────────────────────
# Tunables: how late a missed post may still fire (no 3am posts), and how recent
# a successful post counts as "today already covered".
CATCHUP_MAX_LATE_HOURS = 6
CATCHUP_RECENT_POST_HOURS = 18


def _posted_within(log: list, company_id: str, now_naive: datetime, hours: int) -> bool:
    """True if this company has a successful/dry-run post logged within `hours`.
    Compares naive-to-naive against the server clock (post_log timestamps are
    written with datetime.now()), so it's timezone-agnostic (a duration)."""
    cutoff = now_naive - timedelta(hours=hours)
    for e in log:
        if e.get("company_id") != company_id:
            continue
        # pending_approval counts as covered: the post was generated and is waiting on
        # the user — a redeploy/catch-up must not regenerate and supersede it.
        if e.get("status") not in ("posted", "dry_run_fired", "pending_approval"):
            continue
        try:
            ts = datetime.fromisoformat(e.get("timestamp", ""))
        except Exception:
            continue
        if ts.tzinfo:
            ts = ts.astimezone().replace(tzinfo=None)
        if ts >= cutoff:
            return True
    return False


def _catch_up_missed_posts():
    """Fire any active profile's daily post whose scheduled time passed today but
    didn't go out. Guards: only within CATCHUP_MAX_LATE_HOURS of the slot (avoids
    off-hours posting), and skipped if a post already went out in the last
    CATCHUP_RECENT_POST_HOURS (avoids double-posting). run_for_company itself
    still enforces gen limits and LinkedIn-connected, so this can't post for a
    user who isn't set up."""
    # post_time is interpreted in Asia/Kolkata, matching the cron trigger.
    try:
        from zoneinfo import ZoneInfo
        now_sched = datetime.now(ZoneInfo("Asia/Kolkata"))
    except Exception:
        now_sched = datetime.now()  # fallback: treat the server clock as the schedule clock
    now_naive = datetime.now()
    log = get_post_log()
    for company in list_companies():
        if not company.get("active"):
            continue
        try:
            hh, mm = (int(x) for x in company.get("post_time", "").split(":"))
        except Exception:
            continue
        scheduled = now_sched.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now_sched < scheduled:
            continue  # today's slot hasn't arrived — the normal cron will fire it
        if (now_sched - scheduled) > timedelta(hours=CATCHUP_MAX_LATE_HOURS):
            continue  # too late to post today without looking odd — leave it for tomorrow
        if _posted_within(log, company.get("id"), now_naive, CATCHUP_RECENT_POST_HOURS):
            continue  # already covered today
        try:
            logging.info(f"[Catch-up] Missed daily post for {company.get('name')} — firing now")
            run_for_company(company)
        except Exception:
            logging.exception(f"[Catch-up] failed for {company.get('name')}")


# ── Models ────────────────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    input_type: str
    content: str
    style: str = "illustration"   # image posts: "illustration" (AI) | "card" (insight card)
    profile_id: str = ""          # which saved profile to write as (defaults to the first)
    post_text: str = ""           # source cards: the post's text, so the smart crop knows
                                  # which region of the article page the post actually cites


class PostRequest(BaseModel):
    text: str
    dry_run: bool = False


class ScheduleRequest(BaseModel):
    text: str
    schedule_time: datetime
    dry_run: bool = False


class CompanyRequest(BaseModel):
    name: str
    website_url: str = ""
    linkedin_url: str = ""
    industry: str
    tone: str = "professional"
    post_time: str
    profile_type: str = "company"
    website_type: str = "own"
    carousel_enabled: bool = False
    designation: str = ""
    carousel_theme: str = ""
    allowed_hooks: list[str] = []
    voice_posts: str = ""   # pasted recent posts -> voice examples (fastest way to match a voice)
    tone_shift: bool = False  # opt-in: keep the voice from examples but shift register toward `tone`


class ToggleRequest(BaseModel):
    active: bool


class AccountTypeRequest(BaseModel):
    account_type: str


class PaymentVerifyRequest(BaseModel):
    razorpay_order_id: str
    razorpay_payment_id: str
    razorpay_signature: str


class TopicSuggestRequest(BaseModel):
    designation: str


# ── Frontend ──────────────────────────────────────────────────────────────────
@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/")
def serve_landing():
    return FileResponse(os.path.join(frontend_path, "landing.html"))


@app.get("/tool")
def serve_frontend():
    return FileResponse(os.path.join(frontend_path, "index.html"))


@app.get("/setup")
def serve_dashboard():
    return FileResponse(os.path.join(frontend_path, "dashboard.html"))


@app.get("/onboarding")
def serve_onboarding():
    return FileResponse(os.path.join(frontend_path, "onboarding.html"))


@app.get("/login")
def serve_auth():
    return FileResponse(os.path.join(frontend_path, "auth.html"))


@app.get("/terms")
def serve_terms():
    return FileResponse(os.path.join(frontend_path, "terms.html"))


@app.get("/privacy")
def serve_privacy():
    return FileResponse(os.path.join(frontend_path, "privacy.html"))


# ── Waitlist ──────────────────────────────────────────────────────────────────
class WaitlistRequest(BaseModel):
    name: str
    email: str
    plan: str = "pro"

@app.post("/waitlist")
def join_waitlist(req: WaitlistRequest, request: Request):
    _rate_limit(f"waitlist:{_client_ip(request)}", 5, 3600)
    import db
    email = req.email.strip().lower()
    if not email or "@" not in email:
        raise HTTPException(status_code=400, detail="Invalid email")
    existing = db.waitlist.find_one({"email": email})
    if existing:
        return {"status": "already_joined", "plan": existing.get("plan")}
    db.waitlist.insert_one({
        "name": req.name.strip(),
        "email": email,
        "plan": req.plan,
        "joined_at": datetime.utcnow().isoformat()
    })
    return {"status": "joined"}


# ── App Auth ──────────────────────────────────────────────────────────────────
@app.get("/auth/me")
def me(x_token: str = Header(None)):
    user = auth_module.get_user_by_token(x_token or "")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {**user, "gen_info": auth_module.get_gen_info(user["id"])}


@app.patch("/auth/me")
def update_me(req: AccountTypeRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    if req.account_type not in ("company", "personal"):
        raise HTTPException(status_code=400, detail="Invalid account_type")
    auth_module.update_account_type(user["id"], req.account_type)
    return {"ok": True}


@app.post("/auth/logout")
def app_logout():
    return {"logged_out": True}



# ── Topic suggestions ─────────────────────────────────────────────────────────
_topic_cache: dict[str, list] = {}


@app.post("/topics/suggest")
def suggest_topics(req: TopicSuggestRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    _rate_limit(f"topics:{user['id']}", 12)
    designation = req.designation.strip()
    if len(designation) < 3:
        return {"topics": []}
    key = designation.lower()
    if key in _topic_cache:
        return {"topics": _topic_cache[key]}
    import llm
    try:
        data = llm.generate_json(
            f"Suggest 6 LinkedIn content topic areas for a '{designation}' to post about in "
            f"{datetime.now().year}. Mix the role's core expertise topics with themes currently "
            "trending for that role. Each topic 2-4 words, plain text, no hashtags, no quotes. "
            'Return JSON: {"topics": ["topic", "topic", ...]}',
            temperature=0.7,
            max_tokens=300,
        )
        topics = [str(t).strip().strip('"\'') for t in (data.get("topics") or []) if str(t).strip()][:6]
    except Exception:
        logging.exception("topic suggestion failed")
        topics = []
    if topics:
        _topic_cache[key] = topics
    return {"topics": topics}


# ── Payments (Razorpay) ───────────────────────────────────────────────────────
@app.get("/payments/config")
def payments_config():
    return payments.get_config()


@app.post("/payments/create-order")
def payments_create_order(x_token: str = Header(None)):
    user = _require_user(x_token)
    if not payments.is_configured():
        raise HTTPException(status_code=503, detail="Payments are not configured")
    try:
        order = payments.create_order(user["id"], user.get("email", ""))
    except Exception:
        logging.exception("Razorpay order creation failed")
        raise HTTPException(status_code=502, detail="Could not start payment. Please try again.")
    return {
        "order_id": order["id"],
        "amount":   order["amount"],
        "currency": order["currency"],
        "key_id":   payments.RAZORPAY_KEY_ID,
    }


@app.post("/payments/verify")
def payments_verify(req: PaymentVerifyRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    record = payments.get_order_record(req.razorpay_order_id)
    if not record or record.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Order not found")
    if not payments.verify_payment_signature(
        req.razorpay_order_id, req.razorpay_payment_id, req.razorpay_signature
    ):
        raise HTTPException(status_code=400, detail="Payment verification failed")
    payments.activate_pro(user["id"], req.razorpay_order_id, req.razorpay_payment_id)
    return {"ok": True, "gen_info": auth_module.get_gen_info(user["id"])}


@app.post("/payments/restore")
def payments_restore(x_token: str = Header(None)):
    """Recovery: check Razorpay for captured payments on this user's
    unverified orders (e.g. browser closed mid-checkout) and upgrade."""
    user = _require_user(x_token)
    if not payments.is_configured():
        raise HTTPException(status_code=503, detail="Payments are not configured")
    restored = False
    for record in payments.pending_orders(user["id"]):
        try:
            captured = payments.find_captured_payment(record["order_id"])
        except Exception:
            logging.exception("Razorpay restore lookup failed")
            continue
        if captured:
            payments.activate_pro(user["id"], record["order_id"], captured.get("id", ""), source="restore")
            restored = True
    return {"restored": restored, "gen_info": auth_module.get_gen_info(user["id"])}


@app.get("/payments/history")
def payments_history(x_token: str = Header(None)):
    user = _require_user(x_token)
    return {"payments": payments.payment_history(user["id"])}


@app.post("/payments/webhook")
async def payments_webhook(request: Request):
    body = await request.body()
    signature = request.headers.get("x-razorpay-signature", "")
    if not payments.verify_webhook_signature(body, signature):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")
    event = await request.json()
    if event.get("event") == "payment.captured":
        entity = event["payload"]["payment"]["entity"]
        order_id = entity.get("order_id", "")
        user_id = (entity.get("notes") or {}).get("user_id", "")
        record = payments.get_order_record(order_id)
        if record and user_id and record.get("user_id") == user_id:
            payments.activate_pro(user_id, order_id, entity.get("id", ""), source="webhook")
    return {"ok": True}


# ── LinkedIn OAuth ─────────────────────────────────────────────────────────────
@app.post("/auth/linkedin/start")
def linkedin_start(x_token: str = Header(None)):
    """Begin the LinkedIn OAuth handshake.

    The app session token is read from the X-Token header — never a query
    string, which would leak the token into access logs, browser history, and
    the Referer sent to LinkedIn on the redirect. We map a fresh one-time state
    to the user and hand the frontend the authorization URL to open in a popup.
    """
    user = _require_user(x_token)
    state = secrets.token_urlsafe(16)
    li.register_state(state, user["id"])
    return {"auth_url": li.get_auth_url(state)}


@app.get("/auth/linkedin/callback")
def linkedin_callback(code: str = None, error: str = None, state: str = ""):
    if error or not code:
        return HTMLResponse("<script>window.opener.postMessage('linkedin_error','*');window.close();</script>")
    user_id = li.consume_state(state)
    if not user_id:
        return HTMLResponse("<script>window.opener.postMessage('linkedin_error','*');window.close();</script>")
    try:
        token_data = li.exchange_code_for_token(code)
        li.save_token(user_id, token_data)
        return HTMLResponse("<script>window.opener.postMessage('linkedin_connected','*');window.close();</script>")
    except Exception:
        return HTMLResponse("<script>window.opener.postMessage('linkedin_error','*');window.close();</script>")


@app.get("/auth/linkedin/status")
def linkedin_status(x_token: str = Header(None)):
    user = auth_module.get_user_by_token(x_token or "")
    if not user:
        return {"connected": False}
    return {"connected": li.is_connected(user["id"])}


@app.post("/auth/linkedin/logout")
def linkedin_logout(x_token: str = Header(None)):
    user = auth_module.get_user_by_token(x_token or "")
    if user:
        li.logout(user["id"])
    return {"disconnected": True}


# ── Generate ──────────────────────────────────────────────────────────────────
@app.post("/generate")
def generate(request: GenerateRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    _check_gen_limit(user)
    _rate_limit(f"gen:{user['id']}", 20)
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")
    if request.input_type not in ("text", "url", "youtube"):
        raise HTTPException(status_code=400, detail="Invalid input type")
    try:
        raw_text = process_input(request.input_type, request.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=_friendly_fetch_error(e, request.input_type))
    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="No content could be extracted")
    try:
        profile = _resolve_profile(user["id"], request.profile_id)
        context_text = _with_profile_context(profile, raw_text)
        result = generate_content(context_text, company=profile)
    except Exception as e:
        raise HTTPException(status_code=502, detail=_friendly_generation_error(e))
    try:
        auth_module.increment_gens(user["id"])
        logging.info(f"[Gen] incremented for user {user['id']}")
    except Exception as e:
        logging.error(f"[Gen] increment_gens failed: {e}")
    return result


@app.post("/generate/carousel")
async def generate_carousel_manual(request: GenerateRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    _check_gen_limit(user)
    _rate_limit(f"gen:{user['id']}", 20)
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")
    try:
        raw_text = process_input(request.input_type, request.content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=_friendly_fetch_error(e, request.input_type))
    try:
        import base64
        from carousel import generate_carousel_from_text, render_carousel_pdf
        profile = _resolve_profile(user["id"], request.profile_id)
        context_text = _with_profile_context(profile, raw_text)
        content   = generate_carousel_from_text(context_text, company=profile)
        pdf_bytes = render_carousel_pdf(content, profile or {"name": "Voyce"})
        auth_module.increment_gens(user["id"])
        return {
            "post_text":  content.get("post_text", ""),
            "pdf_base64": base64.b64encode(pdf_bytes).decode(),
            "hook":       content.get("hook_slide", {}).get("headline", ""),
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=_friendly_generation_error(e))


def _fetch_article_meta(url: str) -> dict:
    """og-tag scrape for the source-card receipt: publication, headline, author, date, domain."""
    import html as _html
    from urllib.parse import urlparse
    import httpx
    resp = httpx.get(url, follow_redirects=True, timeout=12,
                     headers={"User-Agent": "Mozilla/5.0 (compatible; Voyce/1.0; +https://voyce.co.in)"})
    resp.raise_for_status()
    page = resp.text[:400_000]

    def meta_tag(*names):
        for n in names:
            for pat in (
                rf'<meta[^>]+(?:property|name)=["\']{n}["\'][^>]*content=["\']([^"\']+)',
                rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]*(?:property|name)=["\']{n}["\']',
            ):
                m = re.search(pat, page, re.I)
                if m:
                    return _html.unescape(m.group(1)).strip()
        return ""

    headline = meta_tag("og:title", "twitter:title")
    if not headline:
        m = re.search(r"<title[^>]*>([^<]+)</title>", page, re.I)
        headline = _html.unescape(m.group(1)).strip() if m else ""
    # Publication titles often ride along as "Headline | Site" — strip the tail.
    headline = re.split(r"\s+[|–—-]\s+(?=[A-Z][\w .]{2,30}$)", headline)[0].strip()

    host = (urlparse(url).netloc or "").replace("www.", "")
    publication = meta_tag("og:site_name") or host.split(".")[0].capitalize()
    date_raw = meta_tag("article:published_time", "og:article:published_time", "date", "publishdate")[:10]
    date_h = date_raw
    try:
        date_h = datetime.strptime(date_raw, "%Y-%m-%d").strftime("%b %d, %Y")
    except ValueError:
        pass
    return {
        "publication": publication,
        "headline": headline,
        "author": meta_tag("author", "article:author", "parsely-author"),
        "date": date_h,
        "domain": host,
    }


@app.post("/generate/image")
async def generate_image_manual(request: GenerateRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    _check_gen_limit(user)
    _rate_limit(f"gen:{user['id']}", 20)
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")

    # "source" — citation receipt: real capture of the article page (vision-cropped to the
    # most relevant region), falling back to a card rendered from the page's own metadata.
    if request.style == "source":
        target = request.content.strip()
        if request.input_type != "url" or not target.lower().startswith("http"):
            raise HTTPException(status_code=400,
                                detail="Source cards need an article link — use the Website URL tab.")
        import base64
        from carousel import capture_source_receipt, render_source_card_png
        try:
            png_bytes = capture_source_receipt(target, request.post_text or "")
        except Exception:
            png_bytes = None
        try:
            meta = _fetch_article_meta(target)
        except Exception:
            meta = {}
        if not png_bytes:
            if not meta.get("headline"):
                raise HTTPException(status_code=502,
                                    detail="Couldn't read that article page for the source card.")
            profile = _resolve_profile(user["id"], request.profile_id)
            png_bytes = render_source_card_png(meta, profile or {"name": "Voyce"})
        auth_module.increment_gens(user["id"])
        return {
            "post_text": "",
            "image_base64": base64.b64encode(png_bytes).decode(),
            "headline": meta.get("headline", ""),
            "alt_text": f"Article headline from {meta.get('publication') or meta.get('domain') or 'the source'}",
        }

    try:
        raw_text = process_input(request.input_type, request.content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=_friendly_fetch_error(e, request.input_type))
    try:
        import base64
        profile = _resolve_profile(user["id"], request.profile_id)
        context_text = _with_profile_context(profile, raw_text)
        if request.style == "card":
            from carousel import generate_image_post_from_text, render_image_post_png
            content = generate_image_post_from_text(context_text, company=profile)
            png_bytes = render_image_post_png(content, profile or {"name": "Voyce"})
            headline = content.get("card_headline", "")
        else:  # "illustration" — model picks the format; "tweet" — forced tweet card (rendered
            # server-side, never touches the image API); "scene" — forced AI illustration
            from carousel import generate_ai_image_post, render_ai_image_png
            force = {"tweet": "tweet_card", "scene": "scene"}.get(request.style)
            content = generate_ai_image_post(context_text, company=profile, force_format=force)
            png_bytes = render_ai_image_png(content, profile or {"name": "Voyce"})
            headline = content.get("alt_text", "")
        auth_module.increment_gens(user["id"])
        return {
            "post_text":    content.get("post_text", ""),
            "image_base64": base64.b64encode(png_bytes).decode(),
            "headline":     headline,
            "alt_text":     content.get("alt_text", ""),
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=_friendly_generation_error(e))


@app.post("/generate/caption")
async def generate_caption_manual(request: GenerateRequest, x_token: str = Header(None)):
    """Caption for a user-uploaded image post — no image is generated here."""
    user = _require_user(x_token)
    _check_gen_limit(user)
    _rate_limit(f"gen:{user['id']}", 20)
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="Add a few words about the image or paste your content.")
    try:
        raw_text = process_input(request.input_type, request.content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=_friendly_fetch_error(e, request.input_type))
    try:
        from carousel import generate_caption_from_text
        profile = _resolve_profile(user["id"], request.profile_id)
        context_text = _with_profile_context(profile, raw_text)
        data = generate_caption_from_text(context_text, company=profile)
        auth_module.increment_gens(user["id"])
        return {"post_text": data.get("post_text", ""), "alt_text": data.get("alt_text", "")}
    except Exception as e:
        raise HTTPException(status_code=502, detail=_friendly_generation_error(e))


# ── LinkedIn Post ──────────────────────────────────────────────────────────────
@app.post("/post/linkedin")
def post_linkedin(request: PostRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    if not li.is_connected(user["id"]):
        raise HTTPException(status_code=401, detail="LinkedIn not connected.")
    text = "\n".join(line.strip() for line in request.text.splitlines())
    if request.dry_run:
        print(f"\n[DRY RUN] Would post to LinkedIn:\n{text}\n")
        return {"status": "dry_run", "preview": text}
    try:
        return li.post_to_linkedin(user["id"], text)
    except Exception:
        logging.exception("LinkedIn text post failed")
        raise HTTPException(status_code=502, detail="Failed to post to LinkedIn. Please reconnect LinkedIn and try again.")


# ── Scheduled Posts ────────────────────────────────────────────────────────────
@app.post("/schedule/linkedin")
def schedule_linkedin(request: ScheduleRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    if not li.is_connected(user["id"]):
        raise HTTPException(status_code=401, detail="LinkedIn not connected.")
    run_at = _as_naive_local(request.schedule_time)
    if run_at <= datetime.now():
        raise HTTPException(status_code=400, detail="Schedule time must be in the future.")
    job_id = secrets.token_urlsafe(8)
    scheduler.add_job(
        _do_scheduled_post,
        trigger="date",
        run_date=run_at,
        args=[request.text, job_id, request.dry_run, user["id"]],
        id=job_id,
    )
    entry = {
        "id": job_id,
        "user_id": user["id"],
        "text": request.text,
        "preview": request.text[:80] + ("..." if len(request.text) > 80 else ""),
        "scheduled_at": run_at.isoformat(),
        "status": "scheduled (dry run)" if request.dry_run else "scheduled",
    }
    db.scheduled.insert_one({**entry})
    entry.pop("text")
    return entry


@app.get("/schedule/list")
def list_scheduled(x_token: str = Header(None)):
    user = _require_user(x_token)
    return list(
        db.scheduled.find({"user_id": user["id"]}, {"_id": 0, "text": 0}).sort("scheduled_at", 1)
    )


@app.delete("/schedule/{job_id}")
def cancel_scheduled(job_id: str, x_token: str = Header(None)):
    user = _require_user(x_token)
    entry = db.scheduled.find_one({"id": job_id})
    if not entry or entry.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    db.scheduled.update_one({"id": job_id}, {"$set": {"status": "cancelled"}})
    return {"cancelled": job_id}


# ── Company / Profile Management ───────────────────────────────────────────────
def _apply_voice_posts(company_id: str, voice_posts: str) -> dict | None:
    """Parse pasted recent posts into voice examples + a style analysis and store them."""
    if not (voice_posts or "").strip():
        return None
    try:
        result = parse_pasted_posts(voice_posts)
        if result.get("top_posts"):
            save_linkedin_data(company_id, result)
            return result
    except Exception:
        pass
    return None


@app.post("/companies")
def create_company(request: CompanyRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    pro = _is_pro(user)
    existing = list_companies(user["id"])
    max_profiles = 3 if pro else 1
    if len(existing) >= max_profiles:
        if pro:
            raise HTTPException(status_code=400, detail="Profile limit reached (3 profiles on Pro)")
        raise HTTPException(status_code=403, detail="PRO_REQUIRED:profiles")
    try:
        data = request.model_dump()
        data["user_id"] = user["id"]
        # Daily automation is an explicit opt-in: every new profile starts paused so
        # nothing ever auto-posts before the user deliberately turns it on.
        data["active"] = False
        if not pro:
            # Automated carousels are a Pro feature
            data["carousel_enabled"] = False
        company = save_company(data)
        vp = _apply_voice_posts(company["id"], request.voice_posts)
        if vp:
            company["linkedin_top_posts"] = vp["top_posts"]
            company["linkedin_analysis"] = vp["analysis"]
        if company.get("active", True):
            _setup_company_cron(company)
        return company
    except Exception:
        logging.exception("company save failed")
        raise HTTPException(status_code=500, detail="Could not save the profile. Please try again.")


@app.delete("/companies/{company_id}/voice")
def reset_company_voice(company_id: str, x_token: str = Header(None)):
    """Clear a profile's learned voice (style analysis + stored sample posts).

    Posts fall back to the selected tone until the user teaches a fresh voice —
    the escape hatch for voice trained on posts the user regrets."""
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    save_linkedin_data(company_id, {})
    return {"ok": True}


@app.get("/companies")
def get_companies(x_token: str = Header(None)):
    user = _require_user(x_token)
    companies = list_companies(user["id"])
    from autonomous import get_post_type_info, get_week_plan, POST_TYPE_DESCRIPTIONS
    for c in companies:
        info = get_post_type_info(c)
        c["next_post_type"] = info["next_post_type"]
        c["next_post_type_desc"] = info.get("next_post_type_desc", "")
        c["recent_post_types"] = info["recent_post_types"]
        c["week_plan"] = get_week_plan(c)
        c["post_type_descriptions"] = POST_TYPE_DESCRIPTIONS
    return companies


@app.put("/companies/{company_id}")
def edit_company(company_id: str, request: CompanyRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        data = request.model_dump()
        if not _is_pro(user):
            data["carousel_enabled"] = False
        updated = update_company(company_id, data)
        vp = _apply_voice_posts(company_id, request.voice_posts)
        if vp and updated:
            updated["linkedin_top_posts"] = vp["top_posts"]
            updated["linkedin_analysis"] = vp["analysis"]
        if updated and updated.get("active", True):
            _setup_company_cron(updated)
        return updated
    except Exception:
        logging.exception("company save failed")
        raise HTTPException(status_code=500, detail="Could not save the profile. Please try again.")


@app.delete("/companies/{company_id}")
def remove_company(company_id: str, x_token: str = Header(None)):
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        scheduler.remove_job(f"auto_{company_id}")
    except Exception:
        pass
    delete_company(company_id)
    return {"deleted": company_id}


@app.post("/companies/{company_id}/toggle")
def toggle(company_id: str, request: ToggleRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    if request.active:
        _require_pro(user, "automation")
    toggle_company(company_id, request.active)
    company = get_company(company_id)
    if company:
        if request.active:
            _setup_company_cron(company)
        else:
            try:
                scheduler.remove_job(f"auto_{company_id}")
            except Exception:
                pass
    return {"active": request.active}


class CarouselPatch(BaseModel):
    theme: str | None = None


@app.patch("/companies/{company_id}/carousel")
def toggle_carousel(company_id: str, request: CarouselPatch | None = None, x_token: str = Header(None)):
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    import db as _db
    # With a theme in the body this sets the theme; with no body it toggles on/off.
    if request is not None and request.theme is not None:
        _db.companies.update_one({"id": company_id}, {"$set": {"carousel_theme": request.theme}})
        return {"carousel_enabled": company.get("carousel_enabled", False), "carousel_theme": request.theme}
    new_val = not company.get("carousel_enabled", False)
    if new_val:
        _require_pro(user, "carousel")
    _db.companies.update_one({"id": company_id}, {"$set": {"carousel_enabled": new_val}})
    return {"carousel_enabled": new_val, "carousel_theme": company.get("carousel_theme", "")}


@app.patch("/companies/{company_id}/approval")
def toggle_approval(company_id: str, x_token: str = Header(None)):
    """Toggle 'ask me before it posts': scheduled runs hold posts for approval
    instead of publishing. Managed by the card, like carousel settings."""
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    new_val = not company.get("approval_mode", False)
    db.companies.update_one({"id": company_id}, {"$set": {"approval_mode": new_val}})
    return {"approval_mode": new_val}


@app.get("/pending")
def pending_posts(x_token: str = Header(None)):
    user = _require_user(x_token)
    from autonomous import list_pending_posts
    return list_pending_posts(user["id"])


@app.post("/pending/{pending_id}/approve")
def approve_pending(pending_id: str, x_token: str = Header(None)):
    user = _require_user(x_token)
    from autonomous import approve_pending_post
    try:
        result = approve_pending_post(pending_id, user["id"])
    except Exception:
        logging.exception("pending approve failed")
        raise HTTPException(status_code=502,
                            detail="LinkedIn publish failed — the post is still in your queue; try again.")
    if result.get("error"):
        raise HTTPException(status_code=404, detail="That post is no longer pending.")
    return result


@app.post("/pending/{pending_id}/discard")
def discard_pending(pending_id: str, x_token: str = Header(None)):
    user = _require_user(x_token)
    from autonomous import discard_pending_post
    return discard_pending_post(pending_id, user["id"])


@app.post("/companies/{company_id}/preview")
def preview_post(company_id: str, x_token: str = Header(None)):
    """Generate a sample post for onboarding preview — does not count against gen limit, does not post."""
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        from autonomous import generate_autonomous_post, _get_post_type, POST_TYPE_LABELS
        from search import search_industry_news, format_news_context
        post_type = _get_post_type(company)
        news_results = search_industry_news(company["industry"], company["name"], 3)
        news_context = format_news_context(news_results)
        post_text = generate_autonomous_post(company, news_context, post_type)
        return {"post": post_text, "post_type": POST_TYPE_LABELS.get(post_type, post_type)}
    except Exception:
        logging.exception("preview generation failed")
        raise HTTPException(status_code=502, detail="Could not generate a preview. Please try again.")


class RunNowRequest(BaseModel):
    post_type: str = ""   # optional override — e.g. "hot_take" to force a tweet-card day


@app.post("/companies/{company_id}/run")
def run_company_now(company_id: str, request: RunNowRequest | None = None,
                    x_token: str = Header(None)):
    user = _require_user(x_token)
    _check_gen_limit(user)
    _rate_limit(f"gen:{user['id']}", 20)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    override = (request.post_type if request else "") or ""
    # Manual "Post now" is explicit intent — it publishes immediately even in approval mode.
    result = run_for_company(company, allow_free_manual=True, post_type_override=override,
                             respect_approval=False)
    return result


class SchedulePlanRequest(BaseModel):
    date: str          # "YYYY-MM-DD"
    post_type: str = ""  # a rotation type, "__carousel__", or "" to clear back to auto


@app.patch("/companies/{company_id}/schedule")
def set_schedule(company_id: str, request: SchedulePlanRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", request.date or ""):
        raise HTTPException(status_code=400, detail="Bad date")
    set_scheduled_type(company_id, request.date, (request.post_type or "").strip())
    return {"ok": True}


@app.get("/companies/log")
def post_log(x_token: str = Header(None)):
    user = _require_user(x_token)
    user_company_ids = {c["id"] for c in list_companies(user["id"])}
    log = get_post_log()
    return [e for e in log if e.get("company_id") in user_company_ids]


@app.get("/analytics")
def get_analytics(x_token: str = Header(None)):
    user = _require_user(x_token)
    user_company_ids = {c["id"] for c in list_companies(user["id"])}
    log = get_post_log()
    posts = [e for e in log if e.get("company_id") in user_company_ids and e.get("status") == "posted"]
    return list(reversed(posts[-14:]))


@app.post("/analytics/refresh")
def refresh_analytics(x_token: str = Header(None)):
    user = _require_user(x_token)
    user_company_ids = {c["id"] for c in list_companies(user["id"])}
    log = get_post_log()
    updated = False
    for entry in log:
        if entry.get("company_id") not in user_company_ids:
            continue
        if entry.get("status") != "posted":
            continue
        urn = entry.get("post_urn", "")
        if not urn:
            continue
        engagement = li.get_post_engagement(user["id"], urn)
        if engagement:
            entry["engagement"] = engagement
            updated = True
    if updated:
        save_post_log(log)
    posts = [e for e in log if e.get("company_id") in user_company_ids and e.get("status") == "posted"]
    return list(reversed(posts[-14:]))


@app.post("/post/linkedin/carousel")
async def post_linkedin_carousel(
    file: UploadFile = File(...),
    text: str = Form(...),
    dry_run: bool = Form(False),
    x_token: str = Header(None),
):
    user = _require_user(x_token)
    if not li.is_connected(user["id"]):
        raise HTTPException(status_code=401, detail="LinkedIn not connected.")
    pdf_bytes = await file.read()
    if dry_run:
        print(f"\n[DRY RUN] Would post carousel to LinkedIn:\n{text}\n")
        return {"status": "dry_run", "preview": text}
    try:
        return li.upload_and_post_carousel(user["id"], pdf_bytes, text)
    except Exception:
        logging.exception("LinkedIn carousel post failed")
        raise HTTPException(status_code=502, detail="Failed to post the carousel to LinkedIn. Please reconnect LinkedIn and try again.")


@app.post("/post/linkedin/image")
async def post_linkedin_image(
    file: UploadFile = File(...),
    text: str = Form(...),
    dry_run: bool = Form(False),
    x_token: str = Header(None),
):
    user = _require_user(x_token)
    if not li.is_connected(user["id"]):
        raise HTTPException(status_code=401, detail="LinkedIn not connected.")
    image_bytes = await file.read()
    if dry_run:
        print(f"\n[DRY RUN] Would post image to LinkedIn:\n{text}\n")
        return {"status": "dry_run", "preview": text}
    try:
        return li.upload_and_post_image(user["id"], image_bytes, text)
    except Exception:
        logging.exception("LinkedIn image post failed")
        raise HTTPException(status_code=502, detail="Failed to post the image to LinkedIn. Please reconnect LinkedIn and try again.")


@app.post("/companies/{company_id}/upload-linkedin")
async def upload_linkedin_data(company_id: str, file: UploadFile = File(...), x_token: str = Header(None)):
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")

    filename = file.filename or ""
    if not (filename.lower().endswith(".pdf") or filename.lower().endswith(".zip")):
        raise HTTPException(status_code=400, detail="Upload a LinkedIn profile PDF or data export ZIP")

    file_bytes = await file.read()
    try:
        result = parse_linkedin_upload(filename, file_bytes)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        logging.exception("LinkedIn data parse failed")
        raise HTTPException(status_code=422, detail="Could not read that file. Try a LinkedIn profile PDF or the data-export ZIP, or paste your posts instead.")

    save_linkedin_data(company_id, result)
    return {
        "status": "ok",
        "type": result["type"],
        "posts_found": result.get("posts_found", 0),
        "analysis": result.get("analysis", {}),
    }


@app.post("/companies/{company_id}/upload-post-screenshots")
async def upload_post_screenshots(company_id: str, files: list[UploadFile] = File(...),
                                  x_token: str = Header(None)):
    """Learn a voice from screenshots of LinkedIn posts (yours or a prospect's)."""
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")

    images = []
    for f in files[:8]:  # cap: 8 screenshots is plenty for a voice
        if not (f.content_type or "").startswith("image/"):
            raise HTTPException(status_code=400, detail="Upload image screenshots (PNG or JPG)")
        data = await f.read()
        if len(data) > 8 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Each screenshot must be under 8 MB")
        images.append(data)
    if not images:
        raise HTTPException(status_code=400, detail="No screenshots received")

    try:
        result = parse_post_screenshots(images)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception:
        logging.exception("screenshot parse failed")
        raise HTTPException(status_code=422, detail="Couldn't read those screenshots. Try clearer full-post images, or paste the text instead.")
    if not result.get("top_posts"):
        raise HTTPException(status_code=422, detail="Couldn't read post text from those screenshots. Try clearer full-post screenshots, or paste the text instead.")

    save_linkedin_data(company_id, result)
    return {
        "status": "ok",
        "type": "screenshots",
        "posts_found": result["posts_found"],
        "analysis": result.get("analysis", {}),
    }
