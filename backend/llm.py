"""
llm.py — Central LLM client. Text and JSON go to Claude when ANTHROPIC_API_KEY is set, with
Gemini as the fallback; reading YouTube, reading images and drawing images stay on Gemini
(Claude can't watch video or make images).

Usage:
    from llm import generate, generate_json
    text = generate(prompt, system=..., temperature=0.8)
    data = generate_json(prompt, system=...)   # parsed dict, retries once on bad JSON
"""

import base64
import json
import os
import re
import time

import anthropic
import google.generativeai as genai
from google.api_core import exceptions as _gexc
from google.api_core import retry as _gretry
from dotenv import load_dotenv

load_dotenv()
# Accept either name: GOOGLE_API_KEY (Google's default) or the older GEMINI_API_KEY.
genai.configure(api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))

MODEL          = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")

# Claude writes: on the same profile and news it followed an author's own rules far better
# than Gemini (27 Oct 2026 side-by-side). One try, then Gemini, so a Claude outage or a
# missing key never stops a post. ~$0.12 a post on Opus 5.5 at medium effort.
CLAUDE_MODEL  = os.getenv("CLAUDE_MODEL", "claude-opus-5-5")
CLAUDE_EFFORT = os.getenv("CLAUDE_EFFORT", "medium")
_claude = (anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"), timeout=90, max_retries=1)
           if os.getenv("ANTHROPIC_API_KEY") else None)
usage_log: list = []   # (model, input tokens, output tokens) of recent Claude calls, so a job can report its cost

# Who a call is made for, (user id, partner id): set by main._require_user for a request and by the background
# jobs for the profile they work on. A partner seat's calls are kept in db.ai_usage, so the partner's bill can be
# set against what its seats actually cost (partner_panel.admin_partners). Voyce's own accounts are not logged:
# no extra database write on their calls. Prices are $ per million tokens.
import contextvars
usage_owner: contextvars.ContextVar = contextvars.ContextVar("usage_owner", default=None)
PRICES = {"claude-opus-5-5": (4, 20), "claude-sonnet-5-5": (2, 10), "claude-haiku-5-5": (0.10, 0.50)}
IMAGE_USD_EST = float(os.getenv("IMAGE_USD_EST", "0.08"))   # a drawn picture, estimated


def _record_usage(kind: str, model: str, tokens_in: int = 0, tokens_out: int = 0, usd: float | None = None):
    owner = usage_owner.get()
    if not owner or not owner[1]:
        return
    if usd is None:
        p_in, p_out = PRICES.get(model, PRICES["claude-opus-5-5"])
        usd = tokens_in * p_in / 1e6 + tokens_out * p_out / 1e6
    try:
        import db
        from datetime import datetime, timezone
        db.ai_usage.insert_one({"user_id": owner[0], "partner_id": owner[1], "kind": kind, "model": model,
                                "in": tokens_in, "out": tokens_out, "usd": round(usd, 5),
                                "at": datetime.now(timezone.utc).isoformat()})
    except Exception:
        pass   # cost logging never stops a post


# gemini-2.5/3.x models are "thinking" models: internal reasoning is billed against
# max_output_tokens. A small cap can be fully consumed by thinking, leaving zero
# tokens for the actual answer (finish_reason=MAX_TOKENS, no parts → empty response).
# gemini-3.5-flash reasons more heavily than 2.5, so the floor must be generous.
# Output length is controlled by the prompt, and you only pay for tokens actually
# generated, so a high cap is safe (it's headroom, not a target).
_MIN_OUTPUT_TOKENS = 4096

# The SDK's own defaults are a 600s timeout per call and 600s of retries on 503,
# so one Gemini brownout could pin an autopilot thread for 20+ minutes. Cap each
# call at 60s and the 503 retries at 90s; generate() still adds its own
# primary → fallback → primary attempts on top.
_REQUEST_OPTIONS = {
    "timeout": 60,
    "retry": _gretry.Retry(
        initial=1.0, maximum=8.0, multiplier=1.5,
        predicate=_gretry.if_exception_type(_gexc.ServiceUnavailable),
        timeout=90,
    ),
}


def _safe_text(resp) -> str:
    """Extract text without raising. resp.text throws when a candidate has no
    parts (e.g. MAX_TOKENS during thinking, or a safety block)."""
    try:
        for cand in (resp.candidates or []):
            parts = getattr(getattr(cand, "content", None), "parts", None) or []
            text = "".join(getattr(p, "text", "") or "" for p in parts).strip()
            if text:
                return text
    except Exception:
        pass
    return ""


def _call(model_name: str, prompt: str, system: str, max_tokens: int,
          temperature: float, json_mode: bool) -> str:
    model = genai.GenerativeModel(model_name, system_instruction=system or None)
    config = genai.GenerationConfig(
        max_output_tokens=max(max_tokens, _MIN_OUTPUT_TOKENS),
        temperature=temperature,
        response_mime_type="application/json" if json_mode else "text/plain",
    )
    resp = model.generate_content(prompt, generation_config=config,
                                  request_options=_REQUEST_OPTIONS)
    return _safe_text(resp)


def _mime(b: bytes) -> str:
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if b[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
        return "image/webp"
    return "image/png"


def _claude_call(prompt: str, system: str, json_mode: bool, image: bytes = None,
                 model: str = None, effort: str = None, timeout: float = None) -> str:
    """One Claude call; "" when it declined or ran out of room. Opus 5.5 always thinks (effort
    sets how much) and takes no temperature, and max_tokens covers the thinking too, so it is
    headroom, not a target. The pinned SDK predates output_config and fallbacks, so they go in
    the body as the API documents them; "fallbacks": "default" lets the API hand a declined
    request to another Claude model within the same call. image, when given, is shown first."""
    if json_mode:
        system = (system or "") + "\n\nReturn only valid JSON, no other text."
    content = prompt
    if image:
        content = [{"type": "image", "source": {"type": "base64", "media_type": _mime(image),
                                                "data": base64.b64encode(image).decode()}},
                   {"type": "text", "text": prompt}]
    kw = dict(model=model or CLAUDE_MODEL, max_tokens=16000,
              messages=[{"role": "user", "content": content}],
              extra_body={"output_config": {"effort": effort or CLAUDE_EFFORT}, "fallbacks": "default"},
              extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"})
    if (system or "").strip():
        kw["system"] = system.strip()
    # A background job may think for minutes; a page request keeps the client's 90 seconds
    resp = (_claude.with_options(timeout=timeout) if timeout else _claude).messages.create(**kw)
    print(f"[claude] {resp.model} in={resp.usage.input_tokens} out={resp.usage.output_tokens} "
          f"stop={resp.stop_reason}", flush=True)
    usage_log.append((resp.model, resp.usage.input_tokens, resp.usage.output_tokens))
    _record_usage("claude", resp.model, resp.usage.input_tokens, resp.usage.output_tokens)
    del usage_log[:-200]
    if resp.stop_reason in ("refusal", "max_tokens"):
        return ""
    return "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()


_YT_READ_PROMPT = (
    "Write out what this video actually says, in clear English prose. "
    "If the speaker is not speaking English, translate rather than transliterate. "
    "Keep every concrete claim, number, example and opinion — this is source material "
    "for a post, so substance matters more than brevity. Do not describe the visuals, "
    "the channel or the thumbnail. No preamble."
)


def read_youtube(url: str, max_output_tokens: int = 8192) -> str:
    """Have Gemini watch a YouTube video and write out what it says.

    The transcript scraper talks to YouTube from our own server, and YouTube
    blocks cloud-provider IPs — so on Render it fails for every video, no matter
    what the video is. This path goes server → Google → YouTube, so our IP is
    never in the conversation and the block does not apply.

    It costs tokens and takes ~30s, which is why it is the fallback rather than
    the first thing tried. Returns a faithful rendition, not a verbatim
    transcript, so the caller labels it as such and nothing quotes it as speech.
    """
    last = None
    for model_name in (MODEL, FALLBACK_MODEL):
        try:
            model = genai.GenerativeModel(model_name)
            # Watching a video legitimately takes longer than a text call, so it
            # gets its own cap; the loop's fallback model is the retry.
            resp = model.generate_content(
                [{"file_data": {"file_uri": url}}, _YT_READ_PROMPT],
                generation_config={"max_output_tokens": max_output_tokens,
                                   "temperature": 0.2},
                request_options={"timeout": 180, "retry": None},
            )
            text = _safe_text(resp).strip()
            if text:
                print(f"[youtube] read by {model_name}: {len(text)} chars", flush=True)
                return text
            last = RuntimeError(f"{model_name} returned nothing for the video")
        except Exception as exc:
            last = exc
            print(f"[youtube] {model_name} could not read {url}: "
                  f"{type(exc).__name__}: {exc}", flush=True)
    raise last or RuntimeError("Could not read that video.")


def generate(prompt: str, system: str = None, max_tokens: int = 2048,
             temperature: float = 0.8, json_mode: bool = False,
             model: str = None, effort: str = None) -> str:
    """Robust generation: Claude first when its key is set, then Gemini primary →
    fallback → primary, retrying on ANY transient failure (rate limits, 503/timeout
    on a cold-started instance, or an empty/safety-blocked response) with a short
    backoff. Raises the last error only after all attempts fail — so the caller
    surfaces an accurate message, and a single blip no longer shows 'temporarily
    unavailable'. No long blocking sleep (which used to risk exceeding the request
    timeout). temperature only reaches Gemini: Claude's current models refuse it."""
    if _claude:
        try:
            text = _claude_call(prompt, system, json_mode, model=model, effort=effort)
            if text:
                return text
            print("[claude] no answer, using Gemini", flush=True)
        except Exception as e:
            print(f"[claude] {type(e).__name__}: {e}; using Gemini", flush=True)
    last_exc = None
    for attempt, model_name in enumerate((MODEL, FALLBACK_MODEL, MODEL)):
        try:
            text = _call(model_name, prompt, system, max_tokens, temperature, json_mode)
            if text:
                return text
            # Empty = safety block or thinking consumed the whole budget — retryable.
            last_exc = RuntimeError("empty response (safety block or output-token exhaustion)")
        except Exception as e:  # transient API errors, timeouts, etc. — all retryable here
            last_exc = e
        if attempt < 2:
            time.sleep(2 * (attempt + 1))  # 2s, then 4s — well under request timeout
    raise last_exc or RuntimeError("generation failed after retries")


def _extract_json(text: str) -> dict:
    text = text.strip()
    # Strip markdown fences if present
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # Decode the FIRST complete JSON object, ignoring any trailing/extra data the
    # model sometimes appends (raw_decode stops cleanly at the end of the object).
    start = text.find("{")
    if start != -1:
        try:
            obj, _ = json.JSONDecoder().raw_decode(text[start:])
            return obj
        except json.JSONDecodeError:
            pass
        # Last resort: greedy brace match (handles some prefix/suffix noise)
        match = re.search(r"\{.*\}", text[start:], re.DOTALL)
        if match:
            return json.loads(match.group(0))
    raise json.JSONDecodeError("No valid JSON object found", text, 0)


def generate_json(prompt: str, system: str = None, max_tokens: int = 2048,
                  temperature: float = 0.8, model: str = None, effort: str = None) -> dict:
    text = generate(prompt, system=system, max_tokens=max_tokens,
                    temperature=temperature, json_mode=True, model=model, effort=effort)
    try:
        return _extract_json(text)
    except json.JSONDecodeError:
        # One retry with explicit instruction — JSON mode occasionally truncates
        text = generate(
            prompt + "\n\nReturn ONLY the JSON object, complete and valid.",
            system=system, max_tokens=max_tokens, temperature=0.4, json_mode=True,
            model=model, effort=effort,
        )
        return _extract_json(text)


def generate_vision(prompt: str, images: list[bytes], system: str = None,
                    max_tokens: int = 2048, temperature: float = 0.2,
                    json_mode: bool = False) -> str:
    """Run a multimodal prompt over one or more images (e.g. screenshots).
    Same primary→fallback handling as generate()."""
    parts = [{"mime_type": _mime(b), "data": b} for b in images]

    def _vcall(model_name: str) -> str:
        model = genai.GenerativeModel(model_name, system_instruction=system or None)
        config = genai.GenerationConfig(
            max_output_tokens=max(max_tokens, _MIN_OUTPUT_TOKENS),
            temperature=temperature,
            response_mime_type="application/json" if json_mode else "text/plain",
        )
        resp = model.generate_content([prompt, *parts], generation_config=config,
                                      request_options=_REQUEST_OPTIONS)
        return _safe_text(resp)

    try:
        return _vcall(MODEL)
    except _gexc.ResourceExhausted:
        return _vcall(FALLBACK_MODEL)


# Claude checks every drawn picture. On 12 finished pictures (4 with real faults) Sonnet 5.5 at low
# effort caught every fault with no false alarm, like Opus, at about 40% of the cost and twice the
# speed; Haiku 5.5 failed one good picture (1 Nov 2026).
CLAUDE_CHECK_MODEL  = os.getenv("CLAUDE_CHECK_MODEL", "claude-sonnet-5-5")
CLAUDE_CHECK_EFFORT = os.getenv("CLAUDE_CHECK_EFFORT", "low")

# Cost vs quality on MB Sam's profile (2 Nov 2026; 3 briefs, blind Opus judge on his 4 I's and voice):
# the humanize pass on Sonnet at low effort scored best on all three posts (7.6 vs 6.9 for Opus doing both,
# and it kept his closing question, which Opus's edit dropped), at $0.070 a post instead of $0.089.
# Carousels on Sonnet at medium scored best on both decks (6.5 vs 5.8) at $0.021 instead of $0.066.
# The writer's first draft stays on Opus: Opus at low effort, or Sonnet drafting, scored lower.
CLAUDE_EDIT_MODEL      = os.getenv("CLAUDE_EDIT_MODEL", "claude-sonnet-5-5")
CLAUDE_EDIT_EFFORT     = os.getenv("CLAUDE_EDIT_EFFORT", "low")
CLAUDE_CAROUSEL_MODEL  = os.getenv("CLAUDE_CAROUSEL_MODEL", "claude-sonnet-5-5")
CLAUDE_CAROUSEL_EFFORT = os.getenv("CLAUDE_CAROUSEL_EFFORT", "medium")


def claude_json(prompt: str, system: str = None, model: str = None, effort: str = None,
                timeout: float = 300) -> dict:
    """JSON from Claude and only Claude, with time to think: for background work where a weaker
    model's answer is worse than trying again later (the morning ideas). Raises on failure."""
    if not _claude:
        raise RuntimeError("Claude isn't set up (ANTHROPIC_API_KEY)")
    text = _claude_call(prompt, system, True, model=model, effort=effort, timeout=timeout)
    if not text:
        raise RuntimeError("Claude returned nothing")
    return _extract_json(text)


def quick_json(prompt: str, system: str = None) -> dict | None:
    """A small judging call on the checker model (Sonnet, low effort): sorting search results and the
    like. Claude only, None on any failure, so callers keep what they had."""
    if not _claude:
        return None
    try:
        return _extract_json(_claude_call(prompt, system, True, model=CLAUDE_CHECK_MODEL, effort=CLAUDE_CHECK_EFFORT))
    except Exception as e:
        print(f"[claude] quick: {type(e).__name__}: {e}", flush=True)
        return None


def look_json(prompt: str, image: bytes, system: str = None) -> dict | None:
    """Claude looks at one image and answers in JSON (Gemini when Claude isn't set up or fails);
    None when neither answers, so a check can never stop the thing it checks."""
    text = ""
    if _claude:
        try:
            text = _claude_call(prompt, system, True, image=image,
                                model=CLAUDE_CHECK_MODEL, effort=CLAUDE_CHECK_EFFORT)
        except Exception as e:
            print(f"[claude] look: {type(e).__name__}: {e}; using Gemini", flush=True)
    if not text:
        try:
            text = generate_vision(prompt, [image], system=system, json_mode=True)
        except Exception as e:
            print(f"[gemini] look: {type(e).__name__}: {e}", flush=True)
            return None
    try:
        return _extract_json(text)
    except (json.JSONDecodeError, ValueError):
        return None


# 3.1 Flash Image spells the words it draws, which 2.5 Flash Image could not, and takes an
# aspect ratio, so a 4:5 post image is drawn 4:5 instead of cropped from a square.
IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-3.1-flash-image")


def pick_image_region(image_bytes: bytes, post_text: str = "", model_name: str = None) -> dict | None:
    """Vision: fractional bounding box of the page region that makes the best citation
    receipt for a post — a chart/graphic the post cites when one is clearly present and
    readable, else the headline block (masthead + headline + byline).
    Returns {"top","bottom","left","right"} (0..1 floats) or None on any failure —
    callers fall back to a fixed top-crop, so this can never break the feature."""
    prompt = f"""This is a screenshot of an article web page. A LinkedIn post reacts to this article.

POST (for relevance):
{(post_text or "").strip()[:600] or "(not provided — default to the headline block)"}

Pick ONE region to crop as the post's citation receipt image:
- If the post's claim clearly cites a chart, table, or data graphic that is visible and
  readable in the screenshot, choose that graphic's region.
- Otherwise choose the headline block: publication masthead/name + headline + byline/date.
Rules: keep full width unless a graphic is clearly bounded; region height between 15% and
60% of the page; never include cookie banners, popups, video players, or unrelated sidebars.

Return ONLY JSON: {{"top": 0.0, "bottom": 0.35, "left": 0.0, "right": 1.0}}"""
    for attempt_model in ((model_name or MODEL), FALLBACK_MODEL):
        try:
            model = genai.GenerativeModel(attempt_model)
            config = genai.GenerationConfig(max_output_tokens=_MIN_OUTPUT_TOKENS, temperature=0.1,
                                            response_mime_type="application/json")
            resp = model.generate_content(
                [{"mime_type": "image/png", "data": image_bytes}, prompt],
                generation_config=config, request_options={"timeout": 25})
            raw = _safe_text(resp)
            if not raw:
                continue
            data = json.loads(re.sub(r"^```(?:json)?|```$", "", raw.strip(), flags=re.M).strip())
            box = {k: float(data[k]) for k in ("top", "bottom", "left", "right")}
            if 0 <= box["top"] < box["bottom"] <= 1 and 0 <= box["left"] < box["right"] <= 1:
                return box
        except Exception:
            continue
    return None


def generate_image(prompt: str, model_name: str = None, aspect_ratio: str = "4:5",
                   size: str = "1K", edit: bytes = None) -> bytes:
    """Generate a single image from a text prompt using a Gemini image model, or, with edit,
    change that image as the prompt says. size is "1K" or "2K" (2K for words that must stay
    crisp). Returns raw image bytes (PNG/JPEG), or None if generation fails or is unavailable
    (callers should fall back to a rendered card so the feature degrades gracefully).
    Over REST: the pinned SDK has no way to ask for an aspect ratio or a size."""
    import requests
    name = model_name or IMAGE_MODEL
    parts = [{"text": prompt}]
    if edit:
        parts.insert(0, {"inlineData": {"mimeType": _mime(edit), "data": base64.b64encode(edit).decode()}})
    try:
        # Hard timeout: a hung image call must never hold the HTTP request open for good —
        # callers fall back to a rendered card, which beats a dead connection.
        r = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{name}:generateContent",
            headers={"x-goog-api-key": os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY") or ""},
            json={"contents": [{"parts": parts}],
                  "generationConfig": {"responseModalities": ["IMAGE"],
                                       "imageConfig": {"aspectRatio": aspect_ratio, "imageSize": size}}},
            timeout=75)
        if r.status_code != 200:
            print(f"[image] {name} {r.status_code}: {r.text[:200]}", flush=True)
            return None
        for cand in r.json().get("candidates") or []:
            for part in (cand.get("content") or {}).get("parts") or []:
                data = (part.get("inlineData") or {}).get("data")
                if data:
                    _record_usage("image", name, usd=IMAGE_USD_EST)
                    return base64.b64decode(data)
    except Exception as e:
        print(f"[image] {name} {type(e).__name__}: {e}", flush=True)
        return None
    return None
