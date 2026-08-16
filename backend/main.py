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
from autonomous import run_for_company, get_post_log, update_post_engagement
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
    carousel_format: str = "standard"  # carousel look: "standard" | "visual" (every point
                                       # gets its own posture diagram) | "posture" (single
                                       # skeleton hero) | "photo" (real annotated footage hero)
    carousel_theme: str = ""           # override the profile's carousel theme for this render
                                       # (e.g. "knowella_deep" for the Knowella brand look)


class VariationsRequest(BaseModel):
    post: str                     # an already-generated post to write alternate versions of
    profile_id: str = ""


class HashtagsRequest(BaseModel):
    post: str                     # the post the hashtags have to fit
    profile_id: str = ""


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
    knowledge: str = ""     # curated facts/rules/angles the AI must ground every post in


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


@app.get("/login")
def serve_auth():
    return _page("auth.html")


@app.get("/terms")
def serve_terms():
    return _page("terms.html")


@app.get("/privacy")
def serve_privacy():
    return _page("privacy.html")


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
    _rate_limit(f"voicecheck:{_client_ip(request)}", 4, 3600)   # 4/hour/IP — it's LLM-costly
    posts_blob = (req.posts or "").strip()
    if len(posts_blob) < 60:
        raise HTTPException(status_code=400,
                            detail="Paste at least one full post — a line or two isn't enough to read your voice.")
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
        raise
    except Exception:
        logging.exception("voice-check failed")
        raise HTTPException(status_code=502,
                            detail="Couldn't run your Voice Check right now. Please try again in a moment.")


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
    # Fractional CMOs run many client brands at once, so a profile = one client.
    max_profiles = 15 if pro else 3
    if len(existing) >= max_profiles:
        if pro:
            raise HTTPException(status_code=400, detail=f"Profile limit reached ({max_profiles} profiles on Pro)")
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
    if result.get("error"):
        raise HTTPException(status_code=404, detail="That post is no longer pending.")
    return result


@app.post("/pending/{pending_id}/discard")
def discard_pending(pending_id: str, x_token: str = Header(None)):
    user = _require_user(x_token)
    from autonomous import discard_pending_post
    return discard_pending_post(pending_id, user["id"])


@app.post("/companies/{company_id}/preview")
def preview_post(company_id: str, post_type: str = "", seed: str = "",
                 x_token: str = Header(None)):
    """Generate a sample post ON DEMAND — the real autopilot pipeline (rotation
    post-type + live news → post) but WITHOUT publishing and WITHOUT counting
    against the gen limit. Works whether or not daily automation is on.
    Optional: post_type forces a type (e.g. from a chosen idea); seed steers the
    post around a specific angle."""
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
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
        return {"post": post_text, "post_type": POST_TYPE_LABELS.get(pt, pt),
                "product_name": subject.get("product_name", "")}
    except Exception:
        logging.exception("preview generation failed")
        raise HTTPException(status_code=502, detail="Could not generate a preview. Please try again.")


@app.post("/companies/{company_id}/ideas")
def suggest_ideas(company_id: str, x_token: str = Header(None)):
    """Propose a short menu of post ideas for the coming days, tailored to the
    profile and grounded in today's live news — spanning different post types so
    the menu covers strategy, not five news reactions. Nothing is posted; no
    gen-limit cost. Each idea can be expanded via /preview (post_type + seed)."""
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    _rate_limit(f"ideas:{user['id']}", 12)
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
        prompt = f"""You are a LinkedIn content strategist for {who} ({subject.get('industry', '')}).
{kb_block}
Propose 5 DISTINCT post ideas for the coming days. Each must use a DIFFERENT post type from this menu — spread across the menu, do not repeat a type:
{type_menu}

Ground each idea in something real: prefer a specific item from the news below and name it; an evergreen angle (a core concept, a myth to bust, a how-to) is fine too — mark those with an empty source. Never invent a statistic or a source.
NEWS:
{news_ctx or '(no fresh news — propose evergreen angles from the niche)'}

Return ONLY JSON:
{{"ideas":[{{"hook":"the post's premise in <=14 words","post_type":"<one key from the menu>","why":"one line: why it lands or what it teaches","source":"exact news title it draws on, or empty if evergreen"}}]}}"""
        data = generate_json(prompt, max_tokens=900, temperature=0.7)
        src_url = {(n.get("title") or ""): (n.get("url") or "") for n in news}
        ideas = []
        for it in (data.get("ideas") or [])[:6]:
            pt = str(it.get("post_type", "")).strip()
            src = str(it.get("source", "")).strip()
            ideas.append({
                "hook": str(it.get("hook", "")).strip()[:160],
                "post_type": pt,
                "post_type_label": POST_TYPE_LABELS.get(pt, pt.replace("_", " ").title() if pt else "Post"),
                "why": str(it.get("why", "")).strip()[:200],
                "source": src[:160],
                "source_url": src_url.get(src, ""),
            })
        ideas = [i for i in ideas if i["hook"]]
        return {"ideas": ideas, "product_name": subject.get("product_name", "")}
    except Exception:
        logging.exception("idea generation failed")
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
    # "Ask me before it posts" is a promise about everything Voyce writes, so it holds
    # here too: Post now generates immediately, but a profile in approval mode still
    # gets the post queued for review rather than published unseen.
    result = run_for_company(company, allow_free_manual=True, post_type_override=override)
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
    return get_post_log(user_company_ids)


@app.get("/analytics")
def get_analytics(x_token: str = Header(None)):
    user = _require_user(x_token)
    user_company_ids = [c["id"] for c in list_companies(user["id"])]
    log = get_post_log(user_company_ids)
    posts = [e for e in log if e.get("status") == "posted"]
    return list(reversed(posts[-14:]))


@app.post("/analytics/refresh")
def refresh_analytics(x_token: str = Header(None)):
    user = _require_user(x_token)
    user_company_ids = [c["id"] for c in list_companies(user["id"])]
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
        engagement = li.get_post_engagement(user["id"], urn)
        if engagement:
            entry["engagement"] = engagement
            update_post_engagement(entry.get("company_id", ""), urn, engagement)
    posts = [e for e in log if e.get("status") == "posted"]
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
