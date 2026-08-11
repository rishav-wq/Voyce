"""design.py — Claude-generated per-product carousel theme specs.

Claude's role is strictly to *design the spec* (colors in the renderer's own
vocabulary); Pillow stays the only thing that renders pixels, and the image
model still never draws text. One call per product — on create, URL change, or
an explicit regenerate — cached on the product document, so cost is per
product, not per post.

Validation is code, not model: every spec is checked for parseable hex and
WCAG contrast before it's accepted. One retry with the failure fed back, then
the caller falls back to the brand-color/industry palette chain (fail-open,
same philosophy as autonomous._value_gate).
"""

import json
import logging
import os

logger = logging.getLogger("design")

# The palette keys match carousel.PALETTES exactly so the renderer can consume
# a spec with zero mapping. Layout enums are deliberately NOT here yet — new
# slide layouts are a dev-time job (PREVIEW-*.html → Pillow renderer), and the
# schema grows an enum only when a renderer actually exists.
_PALETTE_KEYS = ("bg", "accent", "accent2", "title", "subtitle", "body", "muted")

THEME_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "palette": {
            "type": "object",
            "properties": {k: {"type": "string"} for k in _PALETTE_KEYS},
            "required": list(_PALETTE_KEYS),
            "additionalProperties": False,
        },
        "rationale": {"type": "string"},
    },
    "required": ["name", "palette", "rationale"],
    "additionalProperties": False,
}

_SYSTEM = (
    "You are a brand designer creating a color theme for 1080x1080 LinkedIn "
    "carousel slides rendered on a solid background.\n"
    "Palette roles: bg (slide background), accent (brand pop: pills, rules, "
    "highlights), accent2 (secondary pop for alternating labels — must be "
    "clearly distinct from accent), title (headlines), subtitle (kickers/"
    "captions), body (paragraph text), muted (hairlines, page dots).\n"
    "Hard requirements: every value is a 6-digit hex color like #0F172A. "
    "title and body must reach WCAG AA contrast (>= 4.5:1) against bg; accent "
    "and accent2 must reach >= 3:1 against bg. Fit the product's industry and "
    "its brand color when one is given (use it as accent, or as bg with a "
    "readable accent). Slides must look confident and premium, not neon."
)


def _hex_to_rgb(h: str) -> tuple | None:
    h = (h or "").strip().lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return None


def _rel_luminance(rgb: tuple) -> float:
    def chan(c):
        c = c / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (chan(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(a: tuple, b: tuple) -> float:
    la, lb = _rel_luminance(a), _rel_luminance(b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


def validate_theme_spec(spec: dict) -> list[str]:
    """Return a list of problems; empty list = valid."""
    problems = []
    palette = spec.get("palette") or {}
    rgb = {}
    for key in _PALETTE_KEYS:
        c = _hex_to_rgb(palette.get(key, ""))
        if not c:
            problems.append(f"palette.{key} is not a valid hex color")
        rgb[key] = c
    if problems:
        return problems
    for key, minimum in (("title", 4.5), ("body", 4.5), ("accent", 3.0), ("accent2", 3.0)):
        ratio = contrast_ratio(rgb[key], rgb["bg"])
        if ratio < minimum:
            problems.append(
                f"palette.{key} contrast vs bg is {ratio:.2f}:1 (needs >= {minimum}:1)")
    return problems


def _product_prompt(product: dict, company: dict) -> str:
    analysis = product.get("analysis") or {}
    return (
        f"Product: {product.get('name', '')}\n"
        f"Industry/niche: {product.get('industry') or company.get('industry', '')}\n"
        f"Brand color (if any): {product.get('brand_color') or company.get('brand_color') or 'none'}\n"
        f"Company: {company.get('name', '')}\n"
        f"What it does: {str(analysis.get('description', ''))[:600]}\n"
        f"Audience: {str(analysis.get('target_audience', ''))[:300]}"
    )


def generate_theme_spec(product: dict, company: dict) -> dict:
    """One-shot brand-design call, validated in code, one corrective retry.
    Returns {} when Claude isn't configured; raises only on unexpected errors
    (callers treat any failure as 'no spec')."""
    if not os.getenv("ANTHROPIC_API_KEY"):
        return {}
    import anthropic

    client = anthropic.Anthropic()
    messages = [{"role": "user", "content": _product_prompt(product, company)}]

    for attempt in range(2):
        resp = client.messages.create(
            model="claude-opus-5",
            max_tokens=16000,
            system=_SYSTEM,
            messages=messages,
            output_config={"format": {"type": "json_schema", "schema": THEME_SCHEMA}},
        )
        if resp.stop_reason == "refusal":
            logger.warning("theme generation declined by model")
            return {}
        text = next((b.text for b in resp.content if b.type == "text"), "")
        spec = json.loads(text)
        problems = validate_theme_spec(spec)
        if not problems:
            spec["generated_by"] = "claude-opus-5"
            return spec
        logger.info(f"theme spec failed validation (attempt {attempt + 1}): {problems}")
        messages += [
            {"role": "assistant", "content": text},
            {"role": "user", "content":
                "That palette failed validation:\n- " + "\n- ".join(problems) +
                "\nFix exactly these problems and return the corrected theme."},
        ]

    return {}
