import os
import secrets
import logging
from datetime import datetime
from fastapi import FastAPI, HTTPException, UploadFile, File, Header, Form
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from processor import process_input
from generator import generate_content
from company import save_company, get_company, list_companies, delete_company, toggle_company, update_company, save_linkedin_data
from autonomous import run_for_company, get_post_log, save_post_log
from linkedin_data import parse_linkedin_upload
import linkedin as li
import auth as auth_module

logging.basicConfig(level=logging.INFO)
app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend")
app.mount("/static", StaticFiles(directory=frontend_path), name="static")

# ── Scheduler ────────────────────────────────────────────────────────────────
scheduler = BackgroundScheduler()
scheduler.start()
scheduled_posts: list[dict] = []


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
            detail=f"Free plan limit reached ({info['limit']} generations used). Upgrade to Pro for unlimited."
        )


def _setup_company_cron(company: dict):
    job_id = f"auto_{company['id']}"
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    hour, minute = company["post_time"].split(":")
    scheduler.add_job(
        run_for_company,
        trigger=CronTrigger(hour=int(hour), minute=int(minute)),
        args=[company],
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


def _do_scheduled_post(text: str, job_id: str, dry_run: bool = False, user_id: str = ""):
    try:
        if dry_run:
            print(f"\n[DRY RUN] Scheduled post fired:\n{text}\n")
        else:
            li.post_to_linkedin(user_id, text)
        for p in scheduled_posts:
            if p["id"] == job_id:
                p["status"] = "dry_run_fired" if dry_run else "posted"
    except Exception as e:
        for p in scheduled_posts:
            if p["id"] == job_id:
                p["status"] = f"failed: {str(e)}"


# ── Models ────────────────────────────────────────────────────────────────────
class GenerateRequest(BaseModel):
    input_type: str
    content: str


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


class ToggleRequest(BaseModel):
    active: bool


class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str
    account_type: str  # "company" or "personal"


class LoginRequest(BaseModel):
    email: str
    password: str


# ── Frontend ──────────────────────────────────────────────────────────────────
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


# ── App Auth ──────────────────────────────────────────────────────────────────
@app.post("/auth/register")
def register(req: RegisterRequest):
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    try:
        user = auth_module.register(req.email, req.password, req.name, req.account_type)
        token = auth_module.login(req.email, req.password)
        return {"token": token, "user": user}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.post("/auth/login")
def login(req: LoginRequest):
    try:
        token = auth_module.login(req.email, req.password)
        user = auth_module.get_user_by_token(token)
        return {"token": token, "user": user}
    except ValueError as e:
        raise HTTPException(status_code=401, detail=str(e))


@app.get("/auth/me")
def me(x_token: str = Header(None)):
    user = auth_module.get_user_by_token(x_token or "")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {**user, "gen_info": auth_module.get_gen_info(user["id"])}


@app.post("/auth/logout")
def app_logout(x_token: str = Header(None)):
    auth_module.logout(x_token or "")
    return {"logged_out": True}


# ── LinkedIn OAuth ─────────────────────────────────────────────────────────────
@app.get("/auth/linkedin")
def linkedin_login(token: str = ""):
    # token = the user's app session token, passed as query param from the popup
    user = auth_module.get_user_by_token(token)
    if not user:
        return HTMLResponse("Not authenticated", status_code=401)
    state = secrets.token_urlsafe(16)
    li.register_state(state, user["id"])
    return RedirectResponse(li.get_auth_url(state))


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
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")
    if request.input_type not in ("text", "url", "youtube"):
        raise HTTPException(status_code=400, detail="Invalid input type")
    try:
        raw_text = process_input(request.input_type, request.content)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to fetch content: {str(e)}")
    if not raw_text.strip():
        raise HTTPException(status_code=400, detail="No content could be extracted")
    try:
        result = generate_content(raw_text)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Generation failed: {str(e)}")
    auth_module.increment_gens(user["id"])
    return result


@app.post("/generate/carousel")
async def generate_carousel_manual(request: GenerateRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    _check_gen_limit(user)
    if not request.content.strip():
        raise HTTPException(status_code=400, detail="Content cannot be empty")
    try:
        raw_text = process_input(request.input_type, request.content)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
    try:
        import base64
        from carousel import generate_carousel_from_text, render_carousel_pdf
        content   = generate_carousel_from_text(raw_text)
        pdf_bytes = render_carousel_pdf(content, {"name": "Voyce"})
        auth_module.increment_gens(user["id"])
        return {
            "post_text":  content.get("post_text", ""),
            "pdf_base64": base64.b64encode(pdf_bytes).decode(),
            "hook":       content.get("hook_slide", {}).get("headline", ""),
        }
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Carousel generation failed: {str(e)}")


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
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to post: {str(e)}")


# ── Scheduled Posts ────────────────────────────────────────────────────────────
@app.post("/schedule/linkedin")
def schedule_linkedin(request: ScheduleRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    if not li.is_connected(user["id"]):
        raise HTTPException(status_code=401, detail="LinkedIn not connected.")
    if request.schedule_time <= datetime.now():
        raise HTTPException(status_code=400, detail="Schedule time must be in the future.")
    job_id = secrets.token_urlsafe(8)
    scheduler.add_job(
        _do_scheduled_post,
        trigger="date",
        run_date=request.schedule_time,
        args=[request.text, job_id, request.dry_run, user["id"]],
        id=job_id,
    )
    entry = {
        "id": job_id,
        "preview": request.text[:80] + ("..." if len(request.text) > 80 else ""),
        "scheduled_at": request.schedule_time.isoformat(),
        "status": "scheduled (dry run)" if request.dry_run else "scheduled",
    }
    scheduled_posts.append(entry)
    return entry


@app.get("/schedule/list")
def list_scheduled():
    return scheduled_posts


@app.delete("/schedule/{job_id}")
def cancel_scheduled(job_id: str):
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass
    for p in scheduled_posts:
        if p["id"] == job_id:
            p["status"] = "cancelled"
    return {"cancelled": job_id}


# ── Company / Profile Management ───────────────────────────────────────────────
@app.post("/companies")
def create_company(request: CompanyRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    try:
        data = request.model_dump()
        data["user_id"] = user["id"]
        company = save_company(data)
        if company.get("active", True):
            _setup_company_cron(company)
        return company
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/companies")
def get_companies(x_token: str = Header(None)):
    user = _require_user(x_token)
    return list_companies(user["id"])


@app.put("/companies/{company_id}")
def edit_company(company_id: str, request: CompanyRequest, x_token: str = Header(None)):
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    try:
        updated = update_company(company_id, request.model_dump())
        if updated and updated.get("active", True):
            _setup_company_cron(updated)
        return updated
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


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


@app.post("/companies/{company_id}/run")
def run_company_now(company_id: str, x_token: str = Header(None)):
    user = _require_user(x_token)
    company = get_company(company_id)
    if not company or company.get("user_id") != user["id"]:
        raise HTTPException(status_code=404, detail="Not found")
    result = run_for_company(company)
    return result


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
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to post carousel: {str(e)}")


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
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    save_linkedin_data(company_id, result)
    return {
        "status": "ok",
        "type": result["type"],
        "posts_found": result.get("posts_found", 0),
        "analysis": result.get("analysis", {}),
    }
