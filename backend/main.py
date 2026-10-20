import os
import re
import secrets
from html import escape as html_escape
import logging
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Form, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, field_validator
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.executors.pool import ThreadPoolExecutor
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
from company import save_company, get_company, list_companies, delete_company, toggle_company, update_company, save_linkedin_data, set_scheduled_type, valid_tz, DEFAULT_TZ
from autonomous import run_for_company, get_post_log, update_post_engagement
from linkedin_data import parse_linkedin_upload, parse_pasted_posts, parse_post_screenshots
import linkedin as li
import auth as auth_module
import payments
import ratelimit
import db
import sales
import analytics
import notes
from pymongo.errors import DuplicateKeyError

logging.basicConfig(level=logging.INFO)
# No public /docs or /openapi.json: the frontend never uses them, and they hand
# anyone a complete map of every route.
app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
app.include_router(sales.router)   # the team's outreach tracker, /sales/*
app.include_router(notes.router)   # what Voyce knows about each author: weekly question, notes, talks

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


@app.middleware("http")
async def _security_headers(request: Request, call_next):
    """Baseline browser protections on every response, static files included.
    No CSP yet: Clerk and Razorpay load scripts and frames from their own
    domains, so a policy needs tuning against the live pages first."""
    response = await call_next(request)
    response.headers.setdefault("Strict-Transport-Security", "max-age=31536000")
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    return response

frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")


class _RevalidatingStatic(StaticFiles):
    """A deploy has to be visible on the next ordinary reload.

    app.js is pinned behind a hand-written `?v=NN` query string, so any deploy
    where nobody remembers to bump the number can serve stale JS to a returning
    browser. `no-cache` does not mean "do not store", it means "revalidate before
    reuse": the browser still keeps the file and still gets a cheap 304 when it
    has not changed, but a changed file is picked up on the next ordinary reload
    with no hard refresh and no version stamp left to maintain.
    """

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.endswith((".js", ".css", ".html", ".svg")):
            response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", _RevalidatingStatic(directory=frontend_path), name="static")


def _page(name: str) -> FileResponse:
    """An app page, served with the same revalidate-don't-trust rule as /static —
    otherwise a cached shell can outlive the JS it is supposed to load."""
    return FileResponse(os.path.join(frontend_path, name),
                        headers={"Cache-Control": "no-cache"})

# ── Scheduler ────────────────────────────────────────────────────────────────
# APScheduler's defaults drop any run that starts more than 1s late, and one
# autopilot run takes 30–120s, so profiles sharing a post time were silently
# skipped once the pool filled. Allow an hour of lateness, collapse a backlog
# into one run, and keep the pool small so parallel carousel renders can't
# exhaust memory.
scheduler = BackgroundScheduler(
    executors={"default": ThreadPoolExecutor(4)},
    job_defaults={"misfire_grace_time": 3600, "coalesce": True, "max_instances": 1},
)
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
        if info.get("plan") == "pro":
            # An India pass with its generations used up — not a free account.
            raise HTTPException(status_code=402, detail=(
                f"PASS_LIMIT_REACHED:You've used this pass's {info['limit']} generations. "
                "Renew to get more — renewing early adds to the days you have left."))
        raise HTTPException(status_code=402, detail="LIMIT_REACHED")


def _rate_limit(key: str, limit: int, window: float = 60.0):
    """Coarse per-key throttle for cost-incurring / abuse-prone endpoints.
    Raises 429 when exceeded. Complements _check_gen_limit (which caps free
    users by total gens but leaves Pro users and non-gen LLM calls unbounded)."""
    if not ratelimit.allow(key, limit, window):
        raise HTTPException(
            status_code=429,
            detail="Too many requests. Please wait a minute and try again.",
        )


# What each plan may spend per day on features that cost Gemini and Tavily
# money without using a generation. Previews and idea lists share one pool:
# each runs a paid news search and a writing call. Free accounts have no pool
# — their previews and idea lists come out of the 5 free generations instead.
#                 (free, India pass, Pro)
# (free, India pass, International Pro). International Pro is sold as unlimited: its 200
# is a fair-use backstop that stops a script or a stuck page from running up AI and
# news-search costs, far above what a person uses in a day.
FAIR_USE_DAILY = 200
DAILY_LIMITS = {
    "drafts":    (None, None, FAIR_USE_DAILY),   # Free and India spend generations instead
    "rewrites":  (10,   30, FAIR_USE_DAILY),
    "hashtags":  (10,   30, FAIR_USE_DAILY),
}
_DAILY_LABELS = {"drafts": "previews and idea lists", "rewrites": "rewrites", "hashtags": "hashtag suggestions"}


def _plan_tier(user: dict) -> str:
    """"free", "india" (a ₹ pass), "pro" or "admin"."""
    info = auth_module.get_gen_info(user["id"])
    plan = info.get("plan")
    if plan == "admin":
        return "admin"
    if plan == "pro":
        return "india" if info.get("plan_region") == payments.REGION_IN else "pro"
    return "free"


def _use_allowance(key: str, limit: int, window: int = 86400) -> tuple[bool, int]:
    """Count one use against an allowance that starts with the first use and
    lasts `window` seconds. Returns (allowed, seconds until it refills).
    Kept in the database: the in-memory counters this replaces were wiped by
    every deploy, which handed every account a fresh day's worth."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(seconds=window)
    try:
        if db.usage.update_one({"_id": key, "started_at": {"$gt": cutoff}, "count": {"$lt": limit}},
                               {"$inc": {"count": 1}}).modified_count:
            return True, 0
        try:
            # No allowance yet, or the last one has run out: start a new one.
            db.usage.update_one({"_id": key, "started_at": {"$lte": cutoff}},
                                {"$set": {"started_at": now, "count": 1}}, upsert=True)
            return True, 0
        except DuplicateKeyError:
            # The current allowance exists and is used up.
            started = (db.usage.find_one({"_id": key}) or {}).get("started_at") or now
            if started.tzinfo is None:
                started = started.replace(tzinfo=timezone.utc)
            return False, max(60, int((started + timedelta(seconds=window) - now).total_seconds()))
    except Exception:
        logging.exception("[Usage] allowance store unavailable; using the in-memory limit")
        return ratelimit.allow(f"day:{key}", limit, window), 3600


def _refund_allowance(key: str | None):
    """Give back a use that produced nothing (the AI or the search failed)."""
    if not key:
        return
    try:
        db.usage.update_one({"_id": key, "count": {"$gt": 0}}, {"$inc": {"count": -1}})
    except Exception:
        logging.exception("[Usage] refund failed")


def _refills_in(seconds: int) -> str:
    hours = seconds // 3600
    if hours >= 1:
        return f"about {hours} hour{'s' if hours != 1 else ''}"
    return f"{max(1, seconds // 60)} minutes"


def _daily_cap(user: dict, pool: str) -> str | None:
    """A per-account, per-24h ceiling on endpoints that spend Gemini and Tavily
    money without using a generation. Preview, for one, wrote a full post on
    every click with no limit at all — a free account could loop it forever."""
    tier = _plan_tier(user)
    if tier == "admin":
        return None
    free_n, india_n, pro_n = DAILY_LIMITS[pool]
    n = {"free": free_n, "india": india_n, "pro": pro_n}[tier]
    if n is None:
        return None
    key = f"{pool}:{user['id']}"
    ok, wait = _use_allowance(key, n)
    if not ok:
        raise HTTPException(
            status_code=429,
            detail=(f"You've reached today's fair-use limit of {n} {_DAILY_LABELS[pool]}. More in {_refills_in(wait)}."
                    if tier == "pro" else
                    f"You've used today's {n} {_DAILY_LABELS[pool]}. More in {_refills_in(wait)}."
                    + (" Pro raises the limit." if tier == "free" else "")))
    return key   # so a use that fails can be given back


def _spend_free_generation(user: dict, tier: str):
    """Previews and idea lists come out of the plan's generations on Free (5) and on the
    India pass (60). International Pro is unlimited, so nothing is counted there."""
    if tier not in ("free", "india"):
        return
    try:
        auth_module.increment_gens(user["id"])
    except Exception as e:
        logging.error(f"[Gen] increment_gens failed: {e}")


def _client_ip(request: Request) -> str:
    # Traffic arrives through Cloudflare, which sets CF-Connecting-IP to the real
    # visitor. The first X-Forwarded-For entry is whatever the client typed, so
    # keying rate limits on it let anyone reset their limit with a fake header.
    cf_ip = request.headers.get("cf-connecting-ip", "").strip()
    if cf_ip:
        return cf_ip
    return request.client.host if request.client else "unknown"


def _conn_for(user: dict, profile_id: str = "") -> str:
    """The LinkedIn connection a manual post publishes through: the chosen
    profile's own account if it has one, otherwise the user's."""
    if profile_id:
        company = get_company(profile_id)
        if not company or company.get("user_id") != user["id"]:
            raise HTTPException(status_code=404, detail="Profile not found")
        return li.connection_for(company, user["id"])
    return user["id"]


def _is_pro(user: dict) -> bool:
    # By plan, not by "unlimited generations": an India pass is Pro with a
    # generation allowance, and must still unlock daily posting.
    return auth_module.get_gen_info(user["id"])["plan"] in ("pro", "admin")


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
    profile = None
    if profile_id:
        for p in profiles:
            if p.get("id") == profile_id:
                profile = p
                break
    if profile is None:
        profile = profiles[0] if profiles else None
    return profile


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
    if (profile.get("knowledge") or "").strip():
        context.append(
            "\nKNOWLEDGE BASE — ground the post in these facts/rules; never contradict or "
            "invent around them:\n" + profile["knowledge"].strip()[:2000])
    return "\n".join(context) + "\n\nContent to repurpose:\n" + raw_text


def _scheduled_run(company: dict):
    """A scheduled daily post, run at most once per profile per local day across
    every server instance (see autonomous.claim_scheduled_run)."""
    from autonomous import claim_scheduled_run, finish_scheduled_run
    key = claim_scheduled_run(company)
    if not key:
        logging.info(f"[Cron] {company.get('id')} already ran today — skipping duplicate")
        return
    try:
        run_for_company(company)
    finally:
        finish_scheduled_run(key)


def _run_company_by_id(company_id: str):
    """Fetch fresh company data at job fire time, then run."""
    company = get_company(company_id)
    if company and company.get("active"):
        _scheduled_run(company)


def _setup_company_cron(company: dict):
    job_id = f"auto_{company['id']}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    hour, minute = company["post_time"].split(":")
    tz = valid_tz(company.get("timezone", "")) or DEFAULT_TZ
    scheduler.add_job(
        _run_company_by_id,
        trigger=CronTrigger(hour=int(hour), minute=int(minute), timezone=tz),
        args=[company["id"]],
        id=job_id,
        replace_existing=True,
    )


def _refresh_all_crons():
    # Runs at startup: one profile with a bad post_time must not stop the app
    # from booting for everyone else.
    for company in list_companies():
        if company.get("active", True):
            try:
                _setup_company_cron(company)
            except Exception:
                logging.exception(f"[Cron] could not schedule {company.get('id')} "
                                  f"(post_time={company.get('post_time')!r})")


def _ensure_indexes():
    """Let Mongo clean up the short-lived records itself. Best-effort: an index
    problem must never stop the app from starting."""
    try:
        db.oauth_states.create_index("created_at", expireAfterSeconds=600)
        db.run_claims.create_index("created", expireAfterSeconds=3 * 86400)
        db.li_invites.create_index("created_at", expireAfterSeconds=8 * 86400)
        db.usage.create_index("started_at", expireAfterSeconds=2 * 86400)
    except Exception:
        logging.exception("could not create TTL indexes")


@app.on_event("startup")
def startup():
    _ensure_indexes()
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
    # Expiry warnings (LinkedIn connection, Pro pass) once a day — 13:00 UTC is
    # evening in India and morning on the US east coast.
    import notify
    scheduler.add_job(
        notify.sweep_expiries,
        trigger=CronTrigger(hour=13, minute=0, timezone="UTC"),
        id="notify_expiries",
        replace_existing=True,
    )
    # A held post gets one reminder ~12 hours before it expires unseen.
    scheduler.add_job(
        notify.approval_reminders,
        trigger=CronTrigger(minute="*/30"),
        id="notify_approval_reminders",
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
            args=[entry.get("text", ""), entry["id"], "dry run" in entry.get("status", ""),
                  entry.get("li_conn") or entry.get("user_id", "")],
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
    # post_time is a wall-clock time in each profile's own zone, matching its cron.
    from zoneinfo import ZoneInfo
    now_naive = datetime.now()
    log = get_post_log()
    for company in list_companies():
        if not company.get("active"):
            continue
        try:
            hh, mm = (int(x) for x in company.get("post_time", "").split(":"))
        except Exception:
            continue
        try:
            now_sched = datetime.now(ZoneInfo(valid_tz(company.get("timezone", "")) or DEFAULT_TZ))
        except Exception:
            now_sched = datetime.now()  # fallback: treat the server clock as the schedule clock
        scheduled = now_sched.replace(hour=hh, minute=mm, second=0, microsecond=0)
        if now_sched < scheduled:
            continue  # today's slot hasn't arrived — the normal cron will fire it
        if (now_sched - scheduled) > timedelta(hours=CATCHUP_MAX_LATE_HOURS):
            continue  # too late to post today without looking odd — leave it for tomorrow
        if _posted_within(log, company.get("id"), now_naive, CATCHUP_RECENT_POST_HOURS):
            continue  # already covered today
        try:
            logging.info(f"[Catch-up] Missed daily post for {company.get('name')} — firing now")
            _scheduled_run(company)
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
    carousel_theme: str = ""           # override the profile's carousel theme for this render


class VariationsRequest(BaseModel):
    post: str                     # an already-generated post to write alternate versions of
    profile_id: str = ""


class HashtagsRequest(BaseModel):
    post: str                     # the post the hashtags have to fit
    profile_id: str = ""


class PostRequest(BaseModel):
    text: str
    dry_run: bool = False
    profile_id: str = ""   # which profile is posting; its own LinkedIn if it has one


class ScheduleRequest(BaseModel):
    text: str
    schedule_time: datetime
    dry_run: bool = False
    profile_id: str = ""


_POST_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class CompanyRequest(BaseModel):
    name: str
    website_url: str = ""
    linkedin_url: str = ""
    industry: str
    tone: str = "professional"
    post_time: str
    profile_type: str = "company"

    @field_validator("post_time")
    @classmethod
    def _valid_post_time(cls, v: str) -> str:
        v = (v or "").strip()
        if not _POST_TIME_RE.match(v):
            raise ValueError("post_time must be HH:MM in 24-hour time, e.g. 09:00")
        return v
    website_type: str = "own"
    carousel_enabled: bool = False
    designation: str = ""
    carousel_theme: str = ""
    allowed_hooks: list[str] = []
    voice_posts: str = ""   # pasted recent posts -> voice examples (fastest way to match a voice)
    tone_shift: bool = False  # opt-in: keep the voice from examples but shift register toward `tone`
    knowledge: str = ""     # curated facts/rules/angles the AI must ground every post in
    li_photo: str = ""      # their photo from "Fill from LinkedIn" (data URL), for previews and image cards
    timezone: str = ""      # IANA zone from the browser, e.g. "America/New_York"; post_time is local to it


class ToggleRequest(BaseModel):
    active: bool
    # When turning autopilot on: ask before each post (default), or publish
    # without review — which only happens with auto_consent from the user.
    approval_mode: bool | None = None
    auto_consent: bool = False


class ApprovalRequest(BaseModel):
    approval_mode: bool | None = None   # None = flip the current setting
    auto_consent: bool = False


class TimezoneRequest(BaseModel):
    timezone: str


_CONSENT_DETAIL = "CONSENT_REQUIRED:Posting without review needs your explicit OK."


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
    return _page("landing.html")


@app.get("/tool")
def serve_frontend():
    return _page("index.html")


@app.get("/setup")
def serve_dashboard():
    return _page("dashboard.html")


@app.get("/voice-check")
def serve_voice_check():
    return _page("voice-check.html")


@app.get("/how-to-not-sound-like-ai-on-linkedin")
def serve_guide_sound_like_ai():
    return _page("how-to-not-sound-like-ai-on-linkedin.html")


@app.get("/onboarding")
def serve_onboarding():
    return _page("onboarding.html")


@app.get("/analytics.js")
def analytics_js():
    # PostHog loader + Voyce helpers (vTrack / vIdentify / vReset); a no-op without a token.
    return Response(analytics.loader_js(), media_type="application/javascript",
                    headers={"Cache-Control": "public, max-age=300"})


@app.get("/sales")
def serve_sales():
    # The page itself is public HTML; every /sales/* call checks the team list.
    return _page("sales.html")


@app.get("/login")
def serve_auth():
    return _page("auth.html")


@app.get("/signup")
def serve_signup():
    return _page("auth.html")   # same page; it mounts the sign-up form on this path


@app.get("/terms")
def serve_terms():
    return _page("terms.html")


@app.get("/privacy")
def serve_privacy():
    return _page("privacy.html")


# Business pages Razorpay's website review expects (About, Contact, Refund,
# Shipping/Delivery, Pricing), with the aliases a reviewer might type.
@app.get("/about")
def serve_about():
    return _page("about.html")


@app.get("/contact")
def serve_contact():
    return _page("contact.html")


@app.get("/refund")
@app.get("/refunds")
@app.get("/cancellation")
def serve_refund():
    return _page("refund.html")


@app.get("/shipping")
@app.get("/delivery")
def serve_shipping():
    return _page("shipping.html")


@app.get("/pricing")
def serve_pricing():
    return RedirectResponse("/#pricing", status_code=302)


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


# ── Voice Check (free, no-login lead tool) ───────────────────────────────────
# Paste your recent LinkedIn posts → a Voice Score, your Voice DNA, and ONE post
# written in your voice + grounded in today's niche news. It's a throttled taste
# of the paid engine (voice-learning + news + generation), so the upgrade to
# "do this every day, auto-posted" is obvious. No scraping — you paste your posts.
class VoiceCheckRequest(BaseModel):
    posts: str          # a blob of your recent posts (blank-line or --- separated)
    niche: str = ""     # what you post about (optional — inferred from posts if blank)
    name: str = ""      # optional first name for the sample post
    email: str = ""     # optional — captured as a lead / "email me the full report"


@app.post("/voice-check")
def voice_check(req: VoiceCheckRequest, request: Request):
    posts_blob = (req.posts or "").strip()
    if len(posts_blob) < 60:
        raise HTTPException(status_code=400,
                            detail="Paste at least one full post — a line or two isn't enough to read your voice.")
    # No login, and each check costs several Gemini calls plus a Tavily search:
    # 3 a day per visitor, and a ceiling no per-IP trick can get around.
    vc_key = f"voicecheck:{_client_ip(request)}"
    ok, wait = _use_allowance(vc_key, 3)
    if not ok:
        raise HTTPException(status_code=429, detail=(
            f"You've run today's 3 Voice Checks. More in {_refills_in(wait)} — "
            "or sign up free to write in your voice inside Voyce."))
    if not _use_allowance("voicecheck:all", 300)[0]:
        _refund_allowance(vc_key)
        raise HTTPException(status_code=429, detail="Voice Check is very busy today. Please try again tomorrow.")
    name  = (req.name or "").strip()[:60] or "You"
    niche = (req.niche or "").strip()[:120]
    try:
        from linkedin_data import parse_pasted_posts
        from search import search_industry_news, format_news_context
        from autonomous import generate_autonomous_post, POST_TYPE_LABELS
        from llm import generate_json

        parsed = parse_pasted_posts(posts_blob)
        top_posts = parsed.get("top_posts") or []
        if not top_posts:
            raise HTTPException(status_code=400,
                                detail="Couldn't read any posts from that. Paste 1-3 posts, each separated by a blank line.")

        # 1) Judge their real posts → score + Voice DNA (signature phrases must be real).
        joined = "\n\n---\n\n".join(p[:800] for p in top_posts[:5])
        judge_prompt = f"""You are a sharp, honest LinkedIn content coach. Analyze the writer's REAL posts and profile their voice so they recognise themselves.

POSTS:
{joined}

Return ONLY JSON:
{{
  "niche": "<their apparent field/topic in 2-4 words>",
  "score": <integer 0-100: how distinct, human, and worth-reading their writing is>,
  "dimensions": {{
    "voice_distinctiveness": {{"score": <0-100>, "note": "<one line>"}},
    "hook_strength": {{"score": <0-100>, "note": "<one line>"}},
    "specificity": {{"score": <0-100>, "note": "<one line>"}},
    "human_not_generic": {{"score": <0-100>, "note": "<how human vs templated-AI it reads, one line>"}}
  }},
  "voice_dna": {{
    "tone": "<2-4 adjectives>",
    "signature_phrases": ["<a word/phrase they ACTUALLY used>", "<another>"],
    "hook_style": "<how they open, one line>",
    "strength": "<the single best thing about their writing, one line>",
    "fix": "<the single highest-leverage improvement, one line>"
  }},
  "verdict": "<one punchy sentence they'd screenshot>"
}}
Reward specificity, a clear point of view, and a human rhythm. Penalise vague motivation, buzzwords, and generic-AI tells (same-y openers, "Here's what I learned", empty closing questions). Base signature_phrases on words they truly used — never invent."""
        analysis = generate_json(judge_prompt, max_tokens=700, temperature=0.4) or {}
        niche = niche or (str(analysis.get("niche") or "").strip()) or "your field"

        # 2) Build a temp profile carrying their voice, then 3) write ONE in-voice,
        # news-grounded post with the REAL generator (same engine the paid product uses).
        temp = {
            "profile_type":      "personal",
            "name":              name,
            "industry":          niche,
            "tone":              "conversational",
            "linkedin_top_posts": top_posts,
            "linkedin_analysis":  parsed.get("analysis") or {},
        }
        post_type = "expert_insight_p"
        news = search_industry_news(niche, name, 3, post_type=post_type)
        news_ctx = format_news_context(news)
        sample_post = generate_autonomous_post(temp, news_ctx, post_type)
        src = news[0] if news else {}

        # Optional lead capture (funnel) — upsert so repeat checks don't duplicate.
        email = (req.email or "").strip().lower()
        if email and "@" in email:
            try:
                import db
                db.voice_check_leads.update_one(
                    {"email": email},
                    {"$set": {"email": email, "niche": niche, "name": name,
                              "score": analysis.get("score"),
                              "at": datetime.utcnow().isoformat(), "ip": _client_ip(request)}},
                    upsert=True)
            except Exception:
                logging.exception("voice-check lead capture failed")

        return {
            "score":           analysis.get("score"),
            "dimensions":      analysis.get("dimensions") or {},
            "voice_dna":       analysis.get("voice_dna") or {},
            "verdict":         analysis.get("verdict", ""),
            "sample_post":     sample_post,
            "source_title":    src.get("title", ""),
            "source_url":      src.get("url", ""),
            "post_type_label": POST_TYPE_LABELS.get(post_type, "Expert Insight"),
            "niche":           niche,
        }
    except HTTPException:
        _refund_allowance(vc_key)   # the paste couldn't be read: not a run
        raise
    except Exception:
        logging.exception("voice-check failed")
        _refund_allowance(vc_key)
        raise HTTPException(status_code=502,
                            detail="Couldn't run your Voice Check right now. Please try again in a moment.")


# ── App Auth ──────────────────────────────────────────────────────────────────
@app.get("/auth/me")
def me(x_token: str = Header(None)):
    user = auth_module.get_user_by_token(x_token or "")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {**user, "gen_info": auth_module.get_gen_info(user["id"]), "sales_access": sales.is_member(user),
            "needs_onboarding": _needs_onboarding(user)}


def _needs_onboarding(user: dict) -> bool:
    """A new account goes through onboarding wherever sign-up lands it. Sign-up used to rely
    on Clerk's afterSignUpUrl alone, and a sign-in page that reloaded already signed in sent
    everyone to /tool, so new accounts could skip it entirely."""
    if user.get("onboarding_done"):
        return False
    try:
        return db.companies.count_documents({"user_id": user["id"]}, limit=1) == 0
    except Exception:
        return False   # never trap someone in onboarding because the database hiccupped


@app.post("/auth/onboarding-done")
def onboarding_done(x_token: str = Header(None)):
    """\"Skip\" on onboarding: don't send this account back there."""
    user = _require_user(x_token)
    db.users.update_one({"id": user["id"]}, {"$set": {"onboarding_done": True}})
    return {"ok": True}


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
def _pricing_region(request: Request) -> str:
    # Cloudflare adds the visitor's country to every request it proxies.
    return payments.region_for(request.headers.get("cf-ipcountry", ""))


@app.get("/payments/config")
def payments_config(request: Request):
    return payments.get_config(_pricing_region(request))


@app.post("/payments/create-order")
def payments_create_order(request: Request, x_token: str = Header(None)):
    user = _require_user(x_token)
    if not payments.is_configured():
        raise HTTPException(status_code=503, detail="Payments are not configured")
    try:
        order = payments.create_order(user["id"], user.get("email", ""), _pricing_region(request))
        analytics.capture(user["id"], "checkout_started", {"region": order.get("region") or _pricing_region(request)})
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
def linkedin_start(response: Response, x_token: str = Header(None)):
    """Begin the LinkedIn OAuth handshake.

    The app session token is read from the X-Token header — never a query
    string, which would leak the token into access logs, browser history, and
    the Referer sent to LinkedIn on the redirect. We map a fresh one-time state
    to the user and hand the frontend the authorization URL to open in a popup.
    """
    user = _require_user(x_token)
    state = secrets.token_urlsafe(16)
    nonce = secrets.token_urlsafe(24)
    li.register_state(state, user["id"], nonce)
    # The callback only accepts this state from the browser holding this cookie.
    # SameSite=Lax still sends it on LinkedIn's top-level redirect back to us.
    response.set_cookie(_LI_OAUTH_COOKIE, nonce, max_age=int(li.OAUTH_STATE_TTL.total_seconds()),
                        httponly=True, samesite="lax", path="/auth/linkedin",
                        secure=li.REDIRECT_URI.startswith("https://"))
    return {"auth_url": li.get_auth_url(state)}


_LI_OAUTH_COOKIE = "li_oauth"


def _oauth_result_page(ok: bool, title: str, detail: str) -> HTMLResponse:
    """What the connect popup shows. It used to be a bare script, so whenever
    window.opener was missing (a phone handing off to a new tab, the LinkedIn
    app) the person was left on a blank white page."""
    msg = "linkedin_connected" if ok else "linkedin_error"
    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Voyce · LinkedIn</title></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,sans-serif;background:#f6f7f9;color:#16181d;margin:0;padding:56px 20px;text-align:center;">
<p style="font-size:18px;font-weight:600;margin:0 0 8px;">{html_escape(title)}</p>
<p style="font-size:15px;color:#5b6270;line-height:1.5;max-width:420px;margin:0 auto 20px;">{html_escape(detail)}</p>
<p><a href="/setup" style="color:#24365e;font-weight:600;">Back to Voyce</a></p>
<script>try {{ if (window.opener) {{ window.opener.postMessage("{msg}", location.origin); window.close(); }} }} catch (e) {{}}</script>
</body></html>"""
    resp = HTMLResponse(page)
    resp.delete_cookie(_LI_OAUTH_COOKIE, path="/auth/linkedin")
    return resp


@app.get("/auth/linkedin/callback")
def linkedin_callback(request: Request, code: str = None, error: str = None, state: str = ""):
    if error or not code:
        return _oauth_result_page(False, "LinkedIn wasn't connected",
                                  "The connection was cancelled. You can close this window and try again from Voyce.")
    target = li.consume_state(state, request.cookies.get(_LI_OAUTH_COOKIE, ""))
    if not target:
        return _oauth_result_page(False, "This connect link has expired",
                                  "Connect links work once, for 10 minutes, in the browser that started them. "
                                  "Go back to Voyce and click Connect LinkedIn again.")
    company_id, invite_id = target.get("company_id"), target.get("invite_id")
    if company_id:
        # Connecting someone's LinkedIn to a profile through an invite: the
        # invite must still be open and the profile still the inviter's.
        invite = db.li_invites.find_one({"_id": invite_id}) if invite_id else None
        company = get_company(company_id)
        if (not invite or invite.get("used") or not company
                or company.get("user_id") != target.get("owner_id") or invite.get("company_id") != company_id):
            return _oauth_result_page(False, "This invite link is no longer valid",
                                      "Ask the person who sent it for a new link.")
    try:
        token_data = li.exchange_code_for_token(code)
        li.save_token(target["conn"], token_data)
    except Exception:
        logging.exception("LinkedIn token exchange failed")
        return _oauth_result_page(False, "LinkedIn didn't finish connecting",
                                  "Something went wrong on the way back from LinkedIn. Close this window and try again.")
    if not company_id:
        return _oauth_result_page(True, "LinkedIn connected",
                                  "You can close this window and go back to Voyce.")
    db.companies.update_one({"id": company_id}, {"$set": {"li_conn": target["conn"]}})
    db.li_invites.update_one({"_id": invite_id}, {"$set": {"used": True, "used_at": datetime.now().isoformat()}})
    li_name = li.connection_info(target["conn"]).get("name", "")
    import notify
    notify.client_connected(company, li_name)
    owner = db.users.find_one({"id": target.get("owner_id")}, {"name": 1}) or {}
    return _oauth_result_page(True, "Your LinkedIn is connected",
                              f"{owner.get('name') or 'Your Voyce contact'} can now publish posts they approve "
                              f"to your LinkedIn from Voyce. You can disconnect at any time in LinkedIn's settings, "
                              "under Data privacy → Permitted services. You can close this window.")


@app.get("/author")
def post_author(profile_id: str = "", x_token: str = Header(None)):
    """Who the Write preview shows as the poster: the profile's name and title, and the LinkedIn
    photo only when that account is the profile's own (li.author_for_profile)."""
    import base64
    user = _require_user(x_token)
    profile = _resolve_profile(user["id"], profile_id)
    a = li.author_for_profile(profile, user["id"])
    photo = ""
    if a.get("avatar"):
        kind = "png" if a["avatar"].startswith(bytes([0x89]) + b"PNG") else "jpeg"
        photo = f"data:image/{kind};base64," + base64.b64encode(a["avatar"]).decode()
    return {"name": a.get("name") or (profile or {}).get("name") or user.get("name") or "",
            "title": ((profile or {}).get("designation") or "").strip(), "photo": photo}


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


# ── A profile on its own LinkedIn account ────────────────────────────────────
# A fractional CMO's client can't connect their LinkedIn from the CMO's browser:
# the connect flow only finishes in the browser that started it. So the owner
# makes an invite link, the client opens it on their own device and approves on
# LinkedIn, and the token lands on that profile alone.

INVITE_TTL = timedelta(days=7)


def _invite_valid(invite: dict | None) -> bool:
    """Unused and under 7 days old. Stored as naive UTC (Mongo returns it that way)."""
    if not invite or invite.get("used"):
        return False
    created = invite.get("created_at")
    if not isinstance(created, datetime):
        return False
    if created.tzinfo is not None:
        created = created.astimezone(timezone.utc).replace(tzinfo=None)
    return datetime.utcnow() - created <= INVITE_TTL


@app.post("/companies/{company_id}/linkedin-invite")
def create_linkedin_invite(company_id: str, x_token: str = Header(None)):
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    _require_pro(user, "linkedin_accounts")
    _rate_limit(f"li-invite:{user['id']}", 20, 86400)
    token = secrets.token_urlsafe(24)
    db.li_invites.insert_one({"_id": token, "owner_id": user["id"], "company_id": company_id,
                              "used": False, "created_at": datetime.utcnow()})
    import notify
    return {"url": f"{notify.APP_URL}/connect/{token}", "expires_days": INVITE_TTL.days}


def _invite_page(title: str, body_html: str, button: tuple[str, str] | None = None) -> HTMLResponse:
    btn = (f'<p style="margin:26px 0 0;"><a href="{html_escape(button[1])}" style="display:inline-block;background:#24365e;'
           f'color:#fff;text-decoration:none;font-weight:600;padding:12px 22px;border-radius:9px;">{html_escape(button[0])}</a></p>'
           if button else "")
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Connect LinkedIn · Voyce</title>
<meta name="robots" content="noindex"><meta name="referrer" content="no-referrer"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,sans-serif;background:#f6f7f9;color:#16181d;margin:0;padding:56px 20px;">
<div style="max-width:520px;margin:0 auto;background:#fff;border:1px solid #e4e7ec;border-radius:16px;padding:30px 28px;">
<p style="font-size:20px;font-weight:700;margin:0 0 14px;">{html_escape(title)}</p>
<div style="font-size:15px;color:#444;line-height:1.6;">{body_html}</div>{btn}
</div></body></html>""")


@app.get("/connect/{token}")
def linkedin_invite_page(token: str):
    invite = db.li_invites.find_one({"_id": token})
    company = get_company(invite["company_id"]) if invite else None
    if not _invite_valid(invite) or not company or company.get("user_id") != invite.get("owner_id"):
        return _invite_page("This invite link is no longer valid",
                            "<p>Invite links work once, for 7 days. Ask the person who sent it for a new one.</p>")
    owner = db.users.find_one({"id": invite["owner_id"]}, {"name": 1}) or {}
    who, prof = html_escape(owner.get("name") or "Someone"), html_escape(company.get("name") or "a profile")
    return _invite_page(
        "Connect your LinkedIn to Voyce",
        f"<p><strong>{who}</strong> has invited you to connect your LinkedIn account to the Voyce profile "
        f"<strong>{prof}</strong>. Once connected, {who} can publish LinkedIn posts as you through Voyce.</p>"
        "<p>Voyce uses LinkedIn's official sign-in. It never sees your password, never sends messages or "
        "connection requests, and you can disconnect at any time in LinkedIn's settings, under "
        "Data privacy → Permitted services.</p>"
        "<p style=\"font-size:13px;color:#6b7280;\">Not expecting this? You can simply close this page.</p>",
        ("Connect my LinkedIn", f"/connect/{token}/go"))


@app.get("/connect/{token}/go")
def linkedin_invite_go(token: str):
    """Start the connect flow in the invitee's own browser (so the browser-binding
    cookie belongs to them), then hand them to LinkedIn's consent screen."""
    invite = db.li_invites.find_one({"_id": token})
    company = get_company(invite["company_id"]) if invite else None
    if not _invite_valid(invite) or not company or company.get("user_id") != invite.get("owner_id"):
        return _invite_page("This invite link is no longer valid",
                            "<p>Invite links work once, for 7 days. Ask the person who sent it for a new one.</p>")
    state, nonce = secrets.token_urlsafe(16), secrets.token_urlsafe(24)
    li.register_state(state, {"conn": li.profile_conn_key(company["id"]), "owner_id": invite["owner_id"],
                              "company_id": company["id"], "invite_id": token}, nonce)
    resp = RedirectResponse(li.get_auth_url(state), status_code=302)
    resp.set_cookie(_LI_OAUTH_COOKIE, nonce, max_age=int(li.OAUTH_STATE_TTL.total_seconds()),
                    httponly=True, samesite="lax", path="/auth/linkedin",
                    secure=li.REDIRECT_URI.startswith("https://"))
    return resp


@app.post("/companies/{company_id}/linkedin-disconnect")
def disconnect_profile_linkedin(company_id: str, x_token: str = Header(None)):
    """Stop this profile posting to its own LinkedIn; it falls back to the owner's."""
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    if company.get("li_conn"):
        li.logout(company["li_conn"])
        db.companies.update_one({"id": company_id}, {"$unset": {"li_conn": ""}})
    return {"ok": True}


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
    analytics.capture(user["id"], "post_generated", {"input_type": request.input_type, "format": "text"})
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
    except ValueError as e:
        # processor.py raises ValueError carrying a message already written for the
        # user (which video, which cause). The generic wrapper below would replace
        # it with "Could not read that YouTube transcript", losing the reason.
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=_friendly_fetch_error(e, request.input_type))
    try:
        import base64
        from carousel import generate_carousel_from_text, render_carousel_pdf
        profile = _resolve_profile(user["id"], request.profile_id)
        if request.carousel_theme:
            profile = {**(profile or {}), "carousel_theme": request.carousel_theme}
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


@app.post("/generate/variations")
def generate_variations(request: VariationsRequest, x_token: str = Header(None)):
    """Given an already-generated post, write 2 alternate captions — same facts and
    the author's voice, different hook/structure — so the user can pick the best.
    Refinement of existing content, so it doesn't count against the gen limit."""
    user = _require_user(x_token)
    _rate_limit(f"gen:{user['id']}", 20)
    _daily_cap(user, "rewrites")
    if not request.post.strip():
        raise HTTPException(status_code=400, detail="Generate a post first, then I can write variations.")
    try:
        from llm import generate_json
        profile = _resolve_profile(user["id"], request.profile_id)
        voice = _with_profile_context(profile, "").split("Content to repurpose:")[0].strip() if profile else ""
        prompt = f"""{voice}

The author wrote this LinkedIn post:
\"\"\"{request.post[:1600]}\"\"\"

Write 2 ALTERNATIVE versions of it. Keep the same core facts and the author's voice, but give each a DIFFERENT hook/opening and a different structure. Similar length. No new statistics, no hashtags unless the original used them. Never contradict any facts in the original.
Return ONLY JSON: {{"variants": ["full post 1", "full post 2"]}}"""
        data = generate_json(prompt, max_tokens=1600, temperature=0.85)
        variants = [str(v).strip() for v in (data.get("variants") or []) if str(v).strip()][:3]
        return {"variants": variants}
    except Exception as e:
        raise HTTPException(status_code=502, detail=_friendly_generation_error(e))


# A hashtag is one word: lowercase letters and digits, no punctuation, no spaces.
# The model is asked for that shape, but a model will happily return "#B2B SaaS"
# or "#growth-marketing", so the server is what actually guarantees it.
_HASHTAG_RE = re.compile(r"[^0-9a-z]")


def _clean_hashtags(raw: list, limit: int = 12) -> list[str]:
    out, seen = [], set()
    for item in raw:
        tag = _HASHTAG_RE.sub("", str(item).strip().lstrip("#").lower())
        if len(tag) < 2 or len(tag) > 40 or tag in seen:   # #ai and #hr are real tags
            continue
        seen.add(tag)
        out.append("#" + tag)
        if len(out) >= limit:
            break
    return out


@app.post("/generate/hashtags")
def generate_hashtags(request: HashtagsRequest, x_token: str = Header(None)):
    """Suggest hashtags that fit a post the user already has. Like variations this
    is refinement of existing content, so it doesn't spend a generation."""
    user = _require_user(x_token)
    _rate_limit(f"gen:{user['id']}", 20)
    _daily_cap(user, "hashtags")
    if not request.post.strip():
        raise HTTPException(status_code=400, detail="Generate a post first, then I can suggest hashtags.")
    try:
        from llm import generate_json
        profile = _resolve_profile(user["id"], request.profile_id)
        niche = ""
        if profile:
            niche = (profile.get("industry") or "").strip()
        prompt = f"""This LinkedIn post is about to be published{f" by someone who writes about {niche}" if niche else ""}:
\"\"\"{request.post[:1600]}\"\"\"

Suggest 10 LinkedIn hashtags for it, ordered most useful first. Rules:
- Mix the reach: 3 broad industry tags people actually follow, 4 mid-sized topic tags, 3 specific niche tags.
- One word each, lowercase, letters and digits only — no spaces, hyphens, ampersands or punctuation.
- Tags must match what the post is genuinely about. Do not invent company or product names.
- No generic filler like #motivation, #success, #linkedin, #love, #follow.
Return ONLY JSON: {{"hashtags": ["#example", "..."]}}"""
        data = generate_json(prompt, max_tokens=400, temperature=0.5)
        tags = _clean_hashtags(data.get("hashtags") or [])
        if not tags:
            raise HTTPException(status_code=502, detail="No usable hashtags came back — try again.")
        return {"hashtags": tags}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=502, detail=_friendly_generation_error(e))


def _fetch_article_meta(url: str) -> dict:
    """og-tag scrape for the source-card receipt: publication, headline, author, date, domain.
    The URL is user-supplied and the parsed title is reflected back, so this must go
    through net_guard like every other user-URL fetch."""
    import html as _html
    from urllib.parse import urlparse
    from net_guard import safe_get
    resp = safe_get(url, timeout=12,
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
    except ValueError as e:
        # processor.py raises ValueError carrying a message already written for the
        # user (which video, which cause). The generic wrapper below would replace
        # it with "Could not read that YouTube transcript", losing the reason.
        raise HTTPException(status_code=400, detail=str(e))
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
            author = None
            if (content.get("format") or "").strip().lower() == "tweet_card":
                author = li.author_for_profile(profile, user["id"]) or None
            fallback_name = (profile or {}).get("name") or user.get("name") or "Voyce"
            png_bytes = render_ai_image_png(content, profile or {"name": fallback_name}, author=author)
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
    except ValueError as e:
        # processor.py raises ValueError carrying a message already written for the
        # user (which video, which cause). The generic wrapper below would replace
        # it with "Could not read that YouTube transcript", losing the reason.
        raise HTTPException(status_code=400, detail=str(e))
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
    conn = _conn_for(user, request.profile_id)
    if not li.is_connected(conn):
        raise HTTPException(status_code=401, detail="LinkedIn not connected.")
    text = "\n".join(line.strip() for line in request.text.splitlines())
    if request.dry_run:
        print(f"\n[DRY RUN] Would post to LinkedIn:\n{text}\n")
        return {"status": "dry_run", "preview": text}
    try:
        result = li.post_to_linkedin(conn, text)
        analytics.capture(user["id"], "post_published", {"via": "write", "chars": len(text)})
        return result
    except ValueError as e:
        # linkedin.py already turned the API status into something actionable;
        # the blanket handler below would replace it with "reconnect LinkedIn",
        # which is the wrong advice for a rate limit or a duplicate post.
        logging.exception("LinkedIn text post rejected")
        raise HTTPException(status_code=502, detail=str(e))
    except Exception:
        logging.exception("LinkedIn text post failed")
        raise HTTPException(status_code=502, detail="Failed to post to LinkedIn. Please reconnect LinkedIn and try again.")


# ── Scheduled Posts ────────────────────────────────────────────────────────────
@app.post("/schedule/linkedin")
def schedule_linkedin(request: ScheduleRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    conn = _conn_for(user, request.profile_id)
    if not li.is_connected(conn):
        raise HTTPException(status_code=401, detail="LinkedIn not connected.")
    run_at = _as_naive_local(request.schedule_time)
    if run_at <= datetime.now():
        raise HTTPException(status_code=400, detail="Schedule time must be in the future.")
    job_id = secrets.token_urlsafe(8)
    scheduler.add_job(
        _do_scheduled_post,
        trigger="date",
        run_date=run_at,
        args=[request.text, job_id, request.dry_run, conn],
        id=job_id,
    )
    entry = {
        "id": job_id,
        "user_id": user["id"],
        "li_conn": conn,
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
    rows = list(db.scheduled.find({"user_id": user["id"]}, {"_id": 0, "text": 0}).sort("scheduled_at", 1))
    for r in rows:
        try:   # with its offset, so the browser shows the right local time
            r["scheduled_at"] = datetime.fromisoformat(r["scheduled_at"]).astimezone().isoformat()
        except Exception:
            pass
    return rows


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


def _clean_li_photo(data: dict) -> dict:
    """Keep a profile's photo only with a LinkedIn link, and only as a real image: re-encoded
    to a small JPEG (base64), whatever was sent."""
    raw = (data.get("li_photo") or "").strip()
    data["li_photo"] = ""
    if not raw or not (data.get("linkedin_url") or "").strip():
        return data
    try:
        import base64, io
        from PIL import Image
        blob = base64.b64decode(raw.split(",", 1)[1] if raw.startswith("data:") else raw)
        if len(blob) <= 2_000_000:
            im = Image.open(io.BytesIO(blob)).convert("RGB")
            im.thumbnail((256, 256))
            buf = io.BytesIO()
            im.save(buf, "JPEG", quality=85)
            data["li_photo"] = base64.b64encode(buf.getvalue()).decode()
    except Exception:
        pass
    return data


TEAM_MAX_PROFILES = 200   # Sales team (admins / sales_access): sample profiles for prospects; a cap only against runaway loops


@app.post("/companies")
def create_company(request: CompanyRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    pro = _is_pro(user)
    existing = list_companies(user["id"])
    # Three profiles on every plan; on Pro each can post to its own LinkedIn
    # account. Accounts that already have more keep them — this only blocks new ones.
    max_profiles = payments.MAX_PROFILES
    import sales
    if sales.is_member(user):
        max_profiles = TEAM_MAX_PROFILES   # the Voyce team makes a profile per prospect sample
    if len(existing) >= max_profiles:
        raise HTTPException(status_code=400, detail=(
            f"You've reached the limit of {max_profiles} profiles. Delete one to add another."))
    try:
        data = _clean_li_photo(request.model_dump())
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
        db.users.update_one({"id": user["id"]}, {"$set": {"onboarding_done": True}})
        analytics.capture(user["id"], "profile_created", {"profile_type": company.get("profile_type", ""),
                                                          "has_voice_posts": bool(vp), "profiles": len(existing) + 1})
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
    from autonomous import get_post_type_info, get_week_plan, get_week_forecast, carousel_mode, POST_TYPE_DESCRIPTIONS
    for c in companies:
        c["carousels_per_week"] = carousel_mode(c)
        try:
            c["week_forecast"] = get_week_forecast(c)
        except Exception:
            logging.exception("[Forecast] failed")
            c["week_forecast"] = []
        info = get_post_type_info(c)
        c["next_post_type"] = info["next_post_type"]
        c["next_post_type_desc"] = info.get("next_post_type_desc", "")
        c["recent_post_types"] = info["recent_post_types"]
        c["week_plan"] = get_week_plan(c)
        c["post_type_descriptions"] = POST_TYPE_DESCRIPTIONS
        # Which LinkedIn this profile publishes to: its own (a client's), or the user's.
        info = li.connection_info(li.connection_for(c, user["id"]))
        c["li"] = {"own_account": bool(c.get("li_conn")), "connected": info["connected"],
                   "name": info["name"] if c.get("li_conn") else "", "expires_at": info["expires_at"]}
        c.pop("li_conn", None)
    return companies


@app.put("/companies/{company_id}")
def edit_company(company_id: str, request: CompanyRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        data = _clean_li_photo(request.model_dump())
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
    if company.get("li_conn"):
        li.logout(company["li_conn"])
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
        # The India pass covers one profile on daily posting (see payments.py).
        info = auth_module.get_gen_info(user["id"])
        if info.get("plan_region") == payments.REGION_IN:
            others = [c for c in list_companies(user["id"]) if c.get("active") and c.get("id") != company_id]
            if len(others) >= payments.INDIA_AUTOPILOT_PROFILES:
                raise HTTPException(status_code=403, detail=(
                    f"PLAN_LIMIT:Your India plan includes {payments.INDIA_AUTOPILOT_PROFILES} profile on daily "
                    f"posting. Turn off daily posting on \"{others[0].get('name', 'your other profile')}\" first — "
                    "you can still write with every profile manually."))
        # Arming chooses the review mode in the same step. Without an explicit
        # choice it asks before every post; publishing unreviewed needs consent.
        approval = True if request.approval_mode is None else request.approval_mode
        if not approval and not request.auto_consent:
            raise HTTPException(status_code=400, detail=_CONSENT_DETAIL)
        fields = {"approval_mode": approval}
        if not approval:
            fields["auto_post_consent_at"] = datetime.now().isoformat()
        db.companies.update_one({"id": company_id}, {"$set": fields})
    toggle_company(company_id, request.active)
    analytics.capture(user["id"], "daily_posting_enabled" if request.active else "daily_posting_disabled",
                      {"approval_mode": bool(request.approval_mode is not False)})
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
    per_week: int | str | None = None   # 0-3, or "alt" (every other day)


@app.patch("/companies/{company_id}/carousel")
def toggle_carousel(company_id: str, request: CarouselPatch | None = None, x_token: str = Header(None)):
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    import db as _db
    from autonomous import CAROUSEL_CHOICES
    if request is not None and request.per_week is not None:
        n = request.per_week
        if isinstance(n, str) and n.isdigit():
            n = int(n)
        if n not in CAROUSEL_CHOICES:
            raise HTTPException(status_code=400, detail="Carousels per week: 0, 1, 2, 3 or every other day.")
        if n:
            _require_pro(user, "carousel")
        _db.companies.update_one({"id": company_id}, {"$set": {"carousels_per_week": n, "carousel_enabled": bool(n)}})
        return {"carousels_per_week": n, "carousel_enabled": bool(n), "carousel_theme": company.get("carousel_theme", "")}
    # With a theme in the body this sets the theme; with no body it toggles on/off.
    if request is not None and request.theme is not None:
        _db.companies.update_one({"id": company_id}, {"$set": {"carousel_theme": request.theme}})
        return {"carousel_enabled": company.get("carousel_enabled", False), "carousel_theme": request.theme}
    new_val = not company.get("carousel_enabled", False)
    if new_val:
        _require_pro(user, "carousel")
    _db.companies.update_one({"id": company_id}, {"$set": {"carousel_enabled": new_val,
                                                           "carousels_per_week": "alt" if new_val else 0}})
    return {"carousel_enabled": new_val, "carousel_theme": company.get("carousel_theme", "")}


@app.patch("/companies/{company_id}/approval")
def toggle_approval(company_id: str, request: ApprovalRequest | None = None,
                    x_token: str = Header(None)):
    """Set 'ask me before it posts': scheduled runs hold posts for approval
    instead of publishing. Switching it OFF means publishing without review,
    so that direction needs the user's explicit consent."""
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    req = request or ApprovalRequest()
    new_val = (not company.get("approval_mode", False)) if req.approval_mode is None else req.approval_mode
    fields = {"approval_mode": new_val}
    if not new_val:
        if not req.auto_consent:
            raise HTTPException(status_code=400, detail=_CONSENT_DETAIL)
        fields["auto_post_consent_at"] = datetime.now().isoformat()
    db.companies.update_one({"id": company_id}, {"$set": fields})
    return {"approval_mode": new_val}


@app.patch("/companies/{company_id}/timezone")
def set_timezone(company_id: str, request: TimezoneRequest, x_token: str = Header(None)):
    """Move a profile's post time to another zone (same wall-clock time)."""
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    tz = valid_tz(request.timezone)
    if not tz:
        raise HTTPException(status_code=400, detail="Unknown time zone")
    db.companies.update_one({"id": company_id}, {"$set": {"timezone": tz}})
    company["timezone"] = tz
    if company.get("active"):
        _setup_company_cron(company)
    return {"timezone": tz}


@app.get("/pending")
def pending_posts(x_token: str = Header(None)):
    user = _require_user(x_token)
    from autonomous import list_pending_posts
    return list_pending_posts(user["id"])


class ApproveRequest(BaseModel):
    text: str = ""       # caption edited in the queue; empty means publish as generated


@app.post("/pending/{pending_id}/approve")
def approve_pending(pending_id: str, request: ApproveRequest | None = None,
                    x_token: str = Header(None)):
    user = _require_user(x_token)
    from autonomous import approve_pending_post
    try:
        result = approve_pending_post(pending_id, user["id"],
                                      edited_text=(request.text if request else ""))
    except Exception:
        logging.exception("pending approve failed")
        raise HTTPException(status_code=502,
                            detail="LinkedIn publish failed — the post is still in your queue; try again.")
    if result.get("error") == "expired":
        raise HTTPException(status_code=410, detail="This post expired before it was approved, so it won't go out.")
    if result.get("error"):
        raise HTTPException(status_code=404, detail="That post is no longer pending.")
    analytics.capture(user["id"], "post_approved", {"via": "app", "edited": bool(request and request.text)})
    return result


_APPROVE_DONE = {
    "approved":   ("Already posted", "This post was approved and is live on LinkedIn."),
    "publishing": ("Posting now", "This post is being published. Check LinkedIn in a minute."),
    "discarded":  ("Skipped", "You skipped this post, so it won't go out."),
    "superseded": ("Replaced by a newer post", "Voyce wrote a newer post for this profile. That one is waiting in your queue."),
    "expired":    ("This post expired", "It wasn't approved within 48 hours, so it won't go out. Stale news never ships."),
}


def _li_post_url(urn: str) -> str:
    return f"https://www.linkedin.com/feed/update/{urn}/" if urn else ""


def _approve_shell(title: str, inner: str) -> HTMLResponse:
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>{html_escape(title)} · Voyce</title>
<meta name="robots" content="noindex"><meta name="referrer" content="no-referrer"></head>
<body style="font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,sans-serif;background:#f6f7f9;color:#16181d;margin:0;padding:28px 16px 48px;">
<div style="max-width:560px;margin:0 auto;">
<div style="font-weight:700;font-size:18px;letter-spacing:-.02em;margin:0 0 18px 2px;"><span style="font-family:Georgia,serif;font-style:italic;font-size:26px;">V</span>oyce</div>
<div style="background:#fff;border:1px solid #e4e7ec;border-radius:16px;padding:24px 22px;">
<p style="font-size:20px;font-weight:700;margin:0 0 8px;">{html_escape(title)}</p>{inner}
</div></div></body></html>""")


def _btn(label: str, href: str = "", primary: bool = False, form_action: str = "") -> str:
    style = ("display:inline-block;text-decoration:none;font-weight:600;font-size:15px;padding:12px 20px;border-radius:10px;"
             + ("background:#24365e;color:#fff;border:1px solid #24365e;" if primary
                else "background:#fff;color:#24365e;border:1px solid #d3d8e0;"))
    if form_action:
        return (f'<form method="post" action="{html_escape(form_action)}" style="display:inline;margin:0;">'
                f'<button type="submit" style="{style}cursor:pointer;font-family:inherit;">{html_escape(label)}</button></form>')
    return f'<a href="{html_escape(href)}" style="{style}">{html_escape(label)}</a>'


def _pending_by_token(token: str) -> dict | None:
    if not token or len(token) < 20:
        return None
    return db.pending_posts.find_one({"approve_token": token}, {"_id": 0})


@app.get("/approve/{token}")
def approve_page(token: str, request: Request):
    """What the approval email opens. Showing the post changes nothing — email
    scanners open links on their own — approving takes a tap on this page."""
    _rate_limit(f"approvepage:{_client_ip(request)}", 30)
    from autonomous import expires_at, is_expired
    pend = _pending_by_token(token)
    if not pend:
        return _approve_shell("This link isn't valid", '<p style="color:#444;line-height:1.6;">It may be from an old email. '
                              'Your queue in Voyce has everything waiting for you.</p>'
                              f'<p style="margin:22px 0 0;">{_btn("Open Voyce", "/setup", primary=True)}</p>')
    status = pend.get("status")
    if status == "pending" and is_expired(pend):
        status = "expired"
    if status != "pending":
        title, msg = _APPROVE_DONE.get(status, ("Nothing to do here", "This post is no longer waiting."))
        live = ""
        if status == "approved":
            row = db.post_log.find_one({"pending_id": pend["id"], "status": "posted"}, {"post_urn": 1}) or {}
            if row.get("post_urn"):
                live = _btn("View it on LinkedIn", _li_post_url(row["post_urn"]), primary=True) + " "
        return _approve_shell(title, f'<p style="color:#444;line-height:1.6;">{html_escape(msg)}</p>'
                              f'<p style="margin:22px 0 0;display:flex;gap:10px;flex-wrap:wrap;">{live}{_btn("Open Voyce", "/setup")}</p>')
    if pend.get("format") == "diy_video":
        return _approve_shell("Your video day", '<p style="color:#444;line-height:1.6;">This one is a video you make '
                              'yourself. Open Voyce for the caption and the prompt for your video tool.</p>'
                              f'<p style="margin:22px 0 0;">{_btn("Open Voyce", "/setup", primary=True)}</p>')
    exp = expires_at(pend)
    hours = max(1, int((exp - datetime.now()).total_seconds() // 3600)) if exp else 0
    fmt = {"carousel": "Carousel", "tweet_card": "Image card"}.get(pend.get("format"), "Text post")
    slides = "".join(f'<img src="data:image/jpeg;base64,{b}" alt="Slide {i}" style="height:220px;width:auto;border-radius:8px;'
                     f'border:1px solid #e4e7ec;flex:none;">' for i, b in enumerate(pend.get("slide_previews") or [], 1))
    if slides:
        slides = (f'<div style="display:flex;gap:8px;overflow-x:auto;padding:4px 0 10px;margin:14px 0 4px;">{slides}</div>'
                  + ('<p style="font-size:12.5px;color:#6b7280;margin:0 0 6px;">Swipe to see every slide.</p>' if pend.get("format") == "carousel" else ""))
    elif pend.get("format") == "carousel":
        slides = (f'<p style="margin:14px 0 4px;"><a href="/approve/{html_escape(token)}/slides.pdf" '
                  'style="color:#24365e;font-weight:600;">View the slides (PDF)</a></p>')
    inner = (f'<p style="font-size:13.5px;color:#6b7280;margin:0;">{html_escape(pend.get("company_name") or "")} · '
             f'{html_escape(pend.get("post_type") or "")} · {fmt} · <b style="color:#b45309;">expires in {hours} h</b></p>'
             f'{slides}'
             f'<div style="white-space:pre-wrap;font-size:15px;line-height:1.6;color:#1d2027;border:1px solid #eef1f6;'
             f'border-radius:12px;padding:16px;margin:14px 0 0;background:#fbfcfe;">{html_escape(pend.get("post_text") or "")}</div>'
             f'<div style="display:flex;gap:10px;flex-wrap:wrap;margin:20px 0 0;">'
             f'{_btn("Approve & post to LinkedIn", primary=True, form_action=f"/approve/{token}")}'
             f'{_btn("Edit first", "/setup#today")}</div>'
             f'<div style="margin:14px 0 0;">{_btn("Skip this post", form_action=f"/approve/{token}/skip")}</div>'
             '<p style="font-size:12.5px;color:#6b7280;margin:18px 0 0;line-height:1.5;">Approving publishes it now to the '
             'LinkedIn account connected to this profile. This link is just for you, so don\'t forward the email.</p>'
             + _question_box(pend, token))
    return _approve_shell("Ready for your OK", inner)


def _question_box(pend: dict, token: str) -> str:
    """This week's question, answerable right here with no login. Optional."""
    try:
        company = get_company(pend.get("company_id", ""))
        q = notes.question_for(company) if company else None
    except Exception:
        q = None
    if not q or q["status"] == "skipped":
        return ""
    if q["status"] == "answered":
        return ('<div style="margin:22px 0 0;padding:14px 16px;border-radius:12px;background:#e6f4ea;color:#05603a;'
                'font-size:14px;line-height:1.5;">Thanks, your answer is saved. Voyce will build an upcoming post around it.</div>')
    return (f'<form method="post" action="/approve/{html_escape(token)}/note" style="margin:22px 0 0;padding:16px;'
            'border:1px solid #e4e7ec;border-radius:12px;background:#fbfcfe;">'
            '<p style="font-weight:600;margin:0 0 4px;color:#16181d;">This week\'s question '
            '<span style="font-weight:400;color:#6b7280;">(optional)</span></p>'
            f'<p style="margin:0 0 10px;color:#333;line-height:1.5;">{html_escape(q["text"])}</p>'
            '<textarea name="text" rows="3" required minlength="12" maxlength="1500" placeholder="A sentence or two is plenty." '
            'style="width:100%;box-sizing:border-box;padding:10px 12px;border:1px solid #d3d8e0;border-radius:9px;'
            'font:inherit;font-size:14px;line-height:1.5;"></textarea>'
            '<button type="submit" style="margin-top:10px;background:#24365e;color:#fff;border:0;border-radius:10px;'
            'padding:11px 18px;font:inherit;font-weight:600;font-size:14px;cursor:pointer;">Save my answer</button>'
            '<p style="font-size:12.5px;color:#6b7280;margin:10px 0 0;line-height:1.5;">Voyce builds an upcoming post '
            'around your answer, in your words. Only you see this.</p></form>')


@app.post("/approve/{token}/note")
def answer_from_link(token: str, request: Request, text: str = Form("")):
    """The weekly question answered from the approve page (no login: the token is the key)."""
    _rate_limit(f"approvenote:{_client_ip(request)}", 10)
    pend = _pending_by_token(token)
    fresh = False
    try:
        fresh = pend and datetime.fromisoformat(pend.get("created_at", "")) > datetime.now() - timedelta(days=7)
    except ValueError:
        pass
    if fresh:
        company = get_company(pend.get("company_id", ""))
        if company and company.get("user_id") == pend.get("user_id"):
            q = notes.question_for(company)
            try:
                notes.add(company, text, q["kind"], "email", q["text"])
                analytics.capture(pend["user_id"], "note_added", {"source": "email", "kind": q["kind"]})
            except HTTPException:
                pass   # too short: the page shows the box again
    return approve_page(token, request)


@app.post("/approve/{token}")
def approve_from_link(token: str, request: Request):
    _rate_limit(f"approvelink:{_client_ip(request)}", 10)
    from autonomous import approve_pending_post
    pend = _pending_by_token(token)
    if not pend or pend.get("status") != "pending":
        return approve_page(token, request)
    try:
        result = approve_pending_post(pend["id"], pend["user_id"])
    except Exception as e:
        logging.exception("link approve failed")
        # Publish errors are already written for people ("…reconnect LinkedIn from the sidebar").
        reason = html_escape(str(e)[:300] or "LinkedIn didn't accept the post.")
        return _approve_shell("It didn't go out", f'<p style="color:#444;line-height:1.6;">{reason}</p>'
                              '<p style="color:#444;line-height:1.6;">The post is still waiting, so you can try again.</p>'
                              f'<p style="margin:22px 0 0;display:flex;gap:10px;flex-wrap:wrap;">{_btn("Try again", primary=True, form_action=f"/approve/{token}")}'
                              f'{_btn("Open Voyce", "/setup")}</p>')
    if result.get("error"):
        return approve_page(token, request)
    analytics.capture(pend["user_id"], "post_approved", {"via": "email", "post_type": pend.get("post_type", "")})
    live = _btn("View it on LinkedIn", _li_post_url(result.get("post_urn", "")), primary=True) if result.get("post_urn") else ""
    note = (f'<p style="color:#b45309;line-height:1.6;">It went out as text: the {html_escape(result.get("posted_as", ""))} '
            'version failed.</p>' if result.get("posted_as") else "")
    return _approve_shell("Posted ✓", f'<p style="color:#444;line-height:1.6;">It\'s live on LinkedIn.</p>{note}'
                          f'<p style="margin:22px 0 0;display:flex;gap:10px;flex-wrap:wrap;">{live}{_btn("Open Voyce", "/setup")}</p>'
                          + _question_box(pend, token))


@app.post("/approve/{token}/skip")
def skip_from_link(token: str, request: Request):
    _rate_limit(f"approvelink:{_client_ip(request)}", 10)
    from autonomous import discard_pending_post
    pend = _pending_by_token(token)
    if pend and pend.get("status") == "pending":
        discard_pending_post(pend["id"], pend["user_id"])
        analytics.capture(pend["user_id"], "post_skipped", {"via": "email"})
    return approve_page(token, request)


@app.get("/approve/{token}/slides.pdf")
def approve_slides(token: str, request: Request):
    _rate_limit(f"approvepage:{_client_ip(request)}", 30)
    import base64
    pend = _pending_by_token(token)
    full = db.pending_posts.find_one({"id": pend["id"]}, {"asset_b64": 1}) if pend else None
    if not pend or pend.get("format") != "carousel" or not (full or {}).get("asset_b64"):
        raise HTTPException(status_code=404, detail="Not found")
    return Response(content=base64.b64decode(full["asset_b64"]), media_type="application/pdf",
                    headers={"Content-Disposition": 'inline; filename="carousel.pdf"', "Cache-Control": "private, no-store"})


@app.post("/pending/{pending_id}/discard")
def discard_pending(pending_id: str, x_token: str = Header(None)):
    user = _require_user(x_token)
    from autonomous import discard_pending_post
    return discard_pending_post(pending_id, user["id"])


@app.post("/companies/{company_id}/preview")
def preview_post(company_id: str, post_type: str = "", seed: str = "",
                 x_token: str = Header(None)):
    """Generate a sample post ON DEMAND — the real autopilot pipeline (rotation
    post-type + live news → post) but WITHOUT publishing. On Pro it draws on the
    daily previews-and-ideas allowance, not the generation count; on Free it
    uses one of the 5 generations. Works whether or not daily automation is on.
    Optional: post_type forces a type (e.g. from a chosen idea); seed steers the
    post around a specific angle."""
    user = _require_user(x_token)
    _rate_limit(f"gen:{user['id']}", 20)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    tier = _plan_tier(user)
    if tier in ("free", "india"):   # their previews and idea lists use generations
        _check_gen_limit(user)
    cap = _daily_cap(user, "drafts")
    try:
        from autonomous import (generate_autonomous_post, _get_post_type, POST_TYPE_LABELS,
                                COMPANY_ROTATION, PERSONAL_ROTATION)
        from search import search_industry_news, format_news_context
        subject = company   # the profile is the subject (voice + niche + knowledge)
        valid = set((PERSONAL_ROTATION if company.get("profile_type") == "personal"
                     else COMPANY_ROTATION).values())
        pt = post_type if post_type in valid else _get_post_type(company)
        news_results = search_industry_news(
            subject["industry"], subject["name"], 3, post_type=pt,
            extra_angles=subject.get("search_angles") or [])
        news_context = format_news_context(news_results)
        if seed.strip():
            # A chosen idea: make the generator build the post around this angle.
            news_context = f"PRIORITY ANGLE — write the post around this idea: {seed.strip()[:200]}\n\n" + news_context
        post_text = generate_autonomous_post(subject, news_context, pt)
        _spend_free_generation(user, tier)
        analytics.capture(user["id"], "preview_generated", {"post_type": pt, "plan": tier})
        return {"post": post_text, "post_type": POST_TYPE_LABELS.get(pt, pt),
                "product_name": subject.get("product_name", "")}
    except Exception:
        logging.exception("preview generation failed")
        _refund_allowance(cap)
        raise HTTPException(status_code=502, detail="Could not generate a preview. Please try again.")


class IdeasRequest(BaseModel):
    # Whatever the user typed into the composer: a topic, rough notes, a link's
    # text. Ideas were niche + news only, so there was no way to steer them.
    context: str = ""


@app.post("/companies/{company_id}/ideas")
def suggest_ideas(company_id: str, request: IdeasRequest | None = None,
                  x_token: str = Header(None)):
    """Propose a short menu of post ideas for the coming days, tailored to the
    profile and grounded in today's live news — spanning different post types so
    the menu covers strategy, not five news reactions. Nothing is posted. It
    shares the daily allowance with previews on Pro and uses a generation on
    Free. Each idea can be expanded via /preview (post_type + seed)."""
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    _rate_limit(f"ideas:{user['id']}", 12)
    tier = _plan_tier(user)
    if tier in ("free", "india"):   # their previews and idea lists use generations
        _check_gen_limit(user)
    cap = _daily_cap(user, "drafts")
    try:
        from autonomous import (COMPANY_ROTATION, PERSONAL_ROTATION, POST_TYPE_LABELS,
                                POST_TYPE_DESCRIPTIONS)
        from search import search_industry_news, format_news_context
        from llm import generate_json
        subject = company
        is_personal = company.get("profile_type") == "personal"
        rotation = PERSONAL_ROTATION if is_personal else COMPANY_ROTATION
        types = list(dict.fromkeys(rotation.values()))
        type_menu = "\n".join(
            f"- {t}: {POST_TYPE_LABELS.get(t, t)}"
            f"{' — ' + POST_TYPE_DESCRIPTIONS[POST_TYPE_LABELS[t]] if POST_TYPE_LABELS.get(t) in POST_TYPE_DESCRIPTIONS else ''}"
            for t in types)
        news = search_industry_news(subject["industry"], subject["name"], 6,
                                    extra_angles=subject.get("search_angles") or [])
        news_ctx = format_news_context(news)
        who = subject.get("product_name") or subject.get("name", "")
        kb = (subject.get("knowledge") or "").strip()
        kb_block = ("\nKNOWLEDGE BASE — draw ideas from these facts/rules and honour any "
                    "'never say' rules; do not contradict or invent around them:\n" + kb[:2000] + "\n") if kb else ""
        # What makes ideas theirs: their career (profile PDF), what they posted lately, what they told us
        role = (subject.get("designation") or "").strip()
        facts = (subject.get("profile_facts") or "").strip()
        facts_block = ("\nTHEIR CAREER (their LinkedIn profile; real facts to build ideas on):\n" + facts[:8000] + "\n") if facts else ""
        recent = [str(p)[:500] for p in (subject.get("linkedin_top_posts") or [])[:5] if str(p).strip()]
        recent_block = ("\nTHEIR RECENT POSTS (don't repeat these topics or hooks; build on what they're focused on now):\n"
                        + "\n---\n".join(recent) + "\n") if recent else ""
        told = [str(n.get("text", ""))[:300] for n in (subject.get("author_notes") or [])[:8] if n.get("text")]
        notes_block = ("\nTHINGS THEY TOLD US (their own words, real):\n- " + "\n- ".join(told) + "\n") if told else ""
        today_str = datetime.now(timezone.utc).strftime("%d %B %Y")
        NL = chr(10)
        steer = (request.context if request else "").strip()[:1200]
        steer_block = ((NL + "WHAT THEY WANT TO POST ABOUT — this is the brief, and it "
                        "outranks the news below. Every idea must be about this. Use the "
                        "news only where it genuinely supports it, and ignore it entirely "
                        "where it does not:" + NL + steer + NL) if steer else "")

        prompt = f"""You are a LinkedIn content strategist for {who}{(', ' + role) if role else ''} ({subject.get('industry', '')}).
TODAY is {today_str}. Dates in what follows are correct as of today.
{facts_block}{kb_block}{recent_block}{notes_block}{steer_block}
Propose 5 DISTINCT post ideas for the coming days that only THIS person could write. Each must use a DIFFERENT post type from this menu — spread across the menu, do not repeat a type:
{type_menu}

Build every idea on something real and say what in "based_on": copy the exact phrase from THEIR CAREER, the KNOWLEDGE BASE, THINGS THEY TOLD US, or a recent post it builds on, or the news title.
{('- At least 2 ideas come from THEIR CAREER (a role, a responsibility, a move they made).' + chr(10)) if facts else ''}- Prefer a specific item from the news for the rest and name it in "source"; an evergreen angle is fine too (empty source).
- No embellishment: never invent scenes, feelings, quotes, lessons they didn't state, clients, numbers or results. Say what the record says, in plain words.
- Where the record gives different numbers for the same thing (e.g. 30+, 33 and 35 years), don't state an exact count.
- Never invent a statistic or a source.
NEWS:
{news_ctx or '(no fresh news — propose evergreen angles from the niche)'}

Return ONLY JSON:
{{"ideas":[{{"hook":"the post's premise in <=14 words","post_type":"<one key from the menu>","why":"one line: why it lands or what it teaches","based_on":"the exact fact or news title it is built on","source":"exact news title it draws on, or empty if evergreen"}}]}}"""
        data = generate_json(prompt, max_tokens=1400, temperature=0.7)
        src_url = {(n.get("title") or ""): (n.get("url") or "") for n in news}

        def _news_title(t: str) -> str:
            """The model sometimes shortens a headline; map it back to the real one so the link survives."""
            if not t or t in src_url:
                return t
            words = set(re.findall(r"[a-z0-9]+", t.lower()))
            best = max(src_url, key=lambda title: len(words & set(re.findall(r"[a-z0-9]+", title.lower()))), default="")
            overlap = len(words & set(re.findall(r"[a-z0-9]+", best.lower())))
            return best if words and overlap >= max(2, len(words) * 0.6) else t
        ideas = []
        for it in (data.get("ideas") or [])[:6]:
            pt = str(it.get("post_type", "")).strip()
            src = _news_title(str(it.get("source", "")).strip())
            ideas.append({
                "hook": str(it.get("hook", "")).strip()[:160],
                "post_type": pt,
                "post_type_label": POST_TYPE_LABELS.get(pt, pt.replace("_", " ").title() if pt else "Post"),
                "why": str(it.get("why", "")).strip()[:200],
                "based_on": str(it.get("based_on", "")).strip()[:220],
                "source": src[:160],
                "source_url": src_url.get(src, ""),
            })
        ideas = [i for i in ideas if i["hook"]]
        if ideas:
            _spend_free_generation(user, tier)
        else:
            _refund_allowance(cap)
        return {"ideas": ideas, "product_name": subject.get("product_name", "")}
    except Exception:
        logging.exception("idea generation failed")
        _refund_allowance(cap)
        raise HTTPException(status_code=502, detail="Could not generate ideas right now. Please try again.")


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
    if company.get("approval_mode", True):
        waiting = db.pending_posts.find_one({"company_id": company_id, "status": "pending"}, {"_id": 0, "created_at": 1})
        from autonomous import is_expired
        if waiting and not is_expired(waiting):
            # Writing a new one would quietly replace it (one waiting post per profile).
            raise HTTPException(status_code=409, detail=(
                f"WAITING:A post for {company.get('name', 'this profile')} is already waiting for your OK. "
                "Approve or skip it first, so it isn't replaced."))
    # "Ask me before it posts" is a promise about everything Voyce writes, so it holds
    # here too: Post now generates immediately, but a profile in approval mode still
    # gets the post queued for review rather than published unseen.
    result = run_for_company(company, allow_free_manual=True, post_type_override=override,
                             notify_user=False)   # the result is already on their screen
    return result


class SchedulePlanRequest(BaseModel):
    date: str          # "YYYY-MM-DD"
    post_type: str = ""  # a rotation type, "__carousel__", "__video__" (DIY day), or "" to clear back to auto


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
    user_company_ids = [c["id"] for c in list_companies(user["id"])]
    return _with_outcomes(get_post_log(user_company_ids))


def _with_outcomes(log: list) -> list:
    """A row written as "Waiting for approval" shows what became of the post. Rows
    from before approvals updated their own row: an approved one already has its
    "Posted" twin, so it's dropped; an unapproved one past 48 hours expired."""
    from autonomous import PENDING_TTL_HOURS
    ids = [e["pending_id"] for e in log if e.get("status") == "pending_approval" and e.get("pending_id")]
    states = {}
    if ids:
        try:
            states = {p["id"]: p.get("status") for p in db.pending_posts.find({"id": {"$in": ids}}, {"id": 1, "status": 1})}
        except Exception:
            logging.exception("[Log] could not read pending posts")
    cutoff = (datetime.now() - timedelta(hours=PENDING_TTL_HOURS)).isoformat()
    out = []
    for e in log:
        if e.get("status") == "pending_approval":
            st = states.get(e.get("pending_id"))
            if st == "approved":
                continue
            if st in ("expired", "discarded", "superseded"):
                e = {**e, "status": st}
            elif (e.get("timestamp") or "") < cutoff:
                e = {**e, "status": "expired"}
        out.append(e)
    return out


@app.get("/analytics")
def get_analytics(x_token: str = Header(None)):
    user = _require_user(x_token)
    user_company_ids = [c["id"] for c in list_companies(user["id"])]
    log = get_post_log(user_company_ids)
    # Every published post, so "Published" is a real count (it was capped at 14);
    # the per-post chart takes the latest 14 itself.
    return [e for e in log if e.get("status") == "posted"][:500]


@app.post("/analytics/refresh")
def refresh_analytics(x_token: str = Header(None)):
    user = _require_user(x_token)
    companies = list_companies(user["id"])
    user_company_ids = [c["id"] for c in companies]
    conns = {c["id"]: li.connection_for(c, user["id"]) for c in companies}
    log = get_post_log(user_company_ids)
    # Update each entry in place — never rewrite the collection. Only this
    # user's posted entries are even fetched, and each engagement result lands
    # via a targeted update keyed on (company_id, post_urn).
    for entry in log:
        if entry.get("status") != "posted":
            continue
        urn = entry.get("post_urn", "")
        if not urn:
            continue
        engagement = li.get_post_engagement(conns.get(entry.get("company_id", ""), user["id"]), urn)
        if engagement:
            entry["engagement"] = engagement
            update_post_engagement(entry.get("company_id", ""), urn, engagement)
    posts = [e for e in log if e.get("status") == "posted"]
    return posts[:14]   # newest-first, same as /analytics


@app.post("/post/linkedin/carousel")
async def post_linkedin_carousel(
    file: UploadFile = File(...),
    text: str = Form(...),
    dry_run: bool = Form(False),
    profile_id: str = Form(""),
    x_token: str = Header(None),
):
    user = _require_user(x_token)
    conn = _conn_for(user, profile_id)
    if not li.is_connected(conn):
        raise HTTPException(status_code=401, detail="LinkedIn not connected.")
    pdf_bytes = await file.read()
    if dry_run:
        print(f"\n[DRY RUN] Would post carousel to LinkedIn:\n{text}\n")
        return {"status": "dry_run", "preview": text}
    try:
        return li.upload_and_post_carousel(conn, pdf_bytes, text)
    except Exception:
        logging.exception("LinkedIn carousel post failed")
        raise HTTPException(status_code=502, detail="Failed to post the carousel to LinkedIn. Please reconnect LinkedIn and try again.")


@app.post("/post/linkedin/image")
async def post_linkedin_image(
    file: UploadFile = File(...),
    text: str = Form(...),
    dry_run: bool = Form(False),
    profile_id: str = Form(""),
    x_token: str = Header(None),
):
    user = _require_user(x_token)
    conn = _conn_for(user, profile_id)
    if not li.is_connected(conn):
        raise HTTPException(status_code=401, detail="LinkedIn not connected.")
    image_bytes = await file.read()
    if dry_run:
        print(f"\n[DRY RUN] Would post image to LinkedIn:\n{text}\n")
        return {"status": "dry_run", "preview": text}
    try:
        return li.upload_and_post_image(conn, image_bytes, text)
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
        "facts": result["type"] == "pdf",
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
