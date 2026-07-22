"""
llm.py — Central LLM client. All generation goes through Gemini.

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

import google.generativeai as genai
from google.api_core import exceptions as _gexc
from dotenv import load_dotenv

load_dotenv()
# Accept either name: GOOGLE_API_KEY (Google's default) or the older GEMINI_API_KEY.
genai.configure(api_key=os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY"))

MODEL          = os.getenv("GEMINI_MODEL", "gemini-3.5-flash")
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash")


# gemini-2.5/3.x models are "thinking" models: internal reasoning is billed against
# max_output_tokens. A small cap can be fully consumed by thinking, leaving zero
# tokens for the actual answer (finish_reason=MAX_TOKENS, no parts → empty response).
# gemini-3.5-flash reasons more heavily than 2.5, so the floor must be generous.
# Output length is controlled by the prompt, and you only pay for tokens actually
# generated, so a high cap is safe (it's headroom, not a target).
_MIN_OUTPUT_TOKENS = 4096


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
    resp = model.generate_content(prompt, generation_config=config)
    return _safe_text(resp)


def generate(prompt: str, system: str = None, max_tokens: int = 2048,
             temperature: float = 0.8, json_mode: bool = False) -> str:
    """Primary model first; on free-tier rate limits fall back to the secondary
    model (separate quota), and as a last resort wait out the per-minute window."""
    try:
        return _call(MODEL, prompt, system, max_tokens, temperature, json_mode)
    except _gexc.ResourceExhausted:
        pass
    try:
        return _call(FALLBACK_MODEL, prompt, system, max_tokens, temperature, json_mode)
    except _gexc.ResourceExhausted:
        time.sleep(40)  # free-tier RPM windows reset per minute
        return _call(MODEL, prompt, system, max_tokens, temperature, json_mode)


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
                  temperature: float = 0.8) -> dict:
    text = generate(prompt, system=system, max_tokens=max_tokens,
                    temperature=temperature, json_mode=True)
    try:
        return _extract_json(text)
    except json.JSONDecodeError:
        # One retry with explicit instruction — JSON mode occasionally truncates
        text = generate(
            prompt + "\n\nReturn ONLY the JSON object, complete and valid.",
            system=system, max_tokens=max_tokens, temperature=0.4, json_mode=True,
        )
        return _extract_json(text)


def generate_vision(prompt: str, images: list[bytes], system: str = None,
                    max_tokens: int = 2048, temperature: float = 0.2,
                    json_mode: bool = False) -> str:
    """Run a multimodal prompt over one or more images (e.g. screenshots).
    Same primary→fallback handling as generate()."""
    def _sniff(b: bytes) -> str:
        if b[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if b[:3] == b"\xff\xd8\xff":
            return "image/jpeg"
        if b[:4] == b"RIFF" and b[8:12] == b"WEBP":
            return "image/webp"
        return "image/png"
    parts = [{"mime_type": _sniff(b), "data": b} for b in images]

    def _vcall(model_name: str) -> str:
        model = genai.GenerativeModel(model_name, system_instruction=system or None)
        config = genai.GenerationConfig(
            max_output_tokens=max(max_tokens, _MIN_OUTPUT_TOKENS),
            temperature=temperature,
            response_mime_type="application/json" if json_mode else "text/plain",
        )
        resp = model.generate_content([prompt, *parts], generation_config=config)
        return _safe_text(resp)

    try:
        return _vcall(MODEL)
    except _gexc.ResourceExhausted:
        return _vcall(FALLBACK_MODEL)


IMAGE_MODEL = os.getenv("GEMINI_IMAGE_MODEL", "gemini-2.5-flash-image")


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


def generate_image(prompt: str, model_name: str = None) -> bytes:
    """Generate a single image from a text prompt using a Gemini image model.
    Returns raw image bytes (PNG/JPEG), or None if generation fails or is unavailable
    (callers should fall back to a rendered card so the feature degrades gracefully)."""
    name = model_name or IMAGE_MODEL
    try:
        model = genai.GenerativeModel(name)
        # Hard timeout: a hung image call must never hold the HTTP request open past the
        # proxy's limit — callers fall back to a rendered card, which beats a dead connection.
        resp = model.generate_content(prompt, request_options={"timeout": 30})
        for cand in (resp.candidates or []):
            parts = getattr(getattr(cand, "content", None), "parts", None) or []
            for part in parts:
                inline = getattr(part, "inline_data", None)
                data = getattr(inline, "data", None) if inline else None
                if data:
                    return base64.b64decode(data) if isinstance(data, str) else data
    except Exception:
        return None
    return None
