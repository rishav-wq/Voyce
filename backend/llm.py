"""
llm.py — Central LLM client. All generation goes through Gemini.

Usage:
    from llm import generate, generate_json
    text = generate(prompt, system=..., temperature=0.8)
    data = generate_json(prompt, system=...)   # parsed dict, retries once on bad JSON
"""

import json
import os
import re
import time

import google.generativeai as genai
from google.api_core import exceptions as _gexc
from dotenv import load_dotenv

load_dotenv()
genai.configure(api_key=os.getenv("GEMINI_API_KEY"))

MODEL          = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-2.5-flash-lite")


# gemini-2.5 models are "thinking" models: internal reasoning is billed against
# max_output_tokens. A small cap can be fully consumed by thinking, leaving zero
# tokens for the actual answer (finish_reason=MAX_TOKENS, no parts). Floor the cap
# so there's always room for both. Output length is controlled by the prompt, and
# you only pay for tokens actually generated, so a generous cap is safe.
_MIN_OUTPUT_TOKENS = 2048


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
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            return json.loads(match.group(0))
        raise


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
