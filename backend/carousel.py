import io
import json
import os
import re

from groq import Groq
from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

load_dotenv()
_groq = Groq(api_key=os.getenv("GROQ_API_KEY"))

SLIDE_W, SLIDE_H = 1080, 1080
_SCALE = 4                          # 4× supersampling → downscale for maximum sharpness
_RW, _RH = SLIDE_W * _SCALE, SLIDE_H * _SCALE
PAD = 88 * _SCALE

# ── Four named palettes: exact color codes ───────────────────────────────────
# Each palette: bg, accent, title, subtitle, body, muted
PALETTES = {
    "dark_pro": {
        "bg":       (15,  23,  42),    # #0f172a
        "accent":   (56,  189, 248),   # #38bdf8
        "title":    (255, 255, 255),   # #ffffff
        "subtitle": (148, 163, 184),   # #94a3b8
        "body":     (203, 213, 225),   # #cbd5e1
        "muted":    (71,  85,  105),   # #475569
    },
    "warm_dark": {
        "bg":       (28,  25,  23),    # #1c1917
        "accent":   (245, 158, 11),    # #f59e0b
        "title":    (255, 255, 255),
        "subtitle": (168, 162, 158),   # #a8a29e
        "body":     (214, 211, 209),   # #d6d3d1
        "muted":    (87,  83,  78),    # #57534e
    },
    "deep_teal": {
        "bg":       (4,   47,  46),    # #042f2e
        "accent":   (45,  212, 191),   # #2dd4bf
        "title":    (255, 255, 255),
        "subtitle": (94,  234, 212),   # #5eead4
        "body":     (153, 246, 228),   # #99f6e4
        "muted":    (17,  94,  89),    # #115e59
    },
    "deep_indigo": {
        "bg":       (30,  27,  75),    # #1e1b4b
        "accent":   (167, 139, 250),   # #a78bfa
        "title":    (255, 255, 255),
        "subtitle": (196, 181, 253),   # #c4b5fd
        "body":     (224, 231, 255),   # #e0e7ff
        "muted":    (55,  48,  163),   # #3730a3
    },
}

# ── Industry → palette mapping ────────────────────────────────────────────────
_INDUSTRY_MAP = {
    "technology":    "dark_pro",
    "software":      "dark_pro",
    "ai":            "dark_pro",
    "saas":          "dark_pro",
    "cloud":         "dark_pro",
    "data":          "dark_pro",
    "engineering":   "dark_pro",
    "finance":       "warm_dark",
    "consulting":    "warm_dark",
    "legal":         "warm_dark",
    "accounting":    "warm_dark",
    "real estate":   "warm_dark",
    "hr":            "warm_dark",
    "recruitment":   "warm_dark",
    "sales":         "warm_dark",
    "healthcare":    "deep_teal",
    "medical":       "deep_teal",
    "health":        "deep_teal",
    "wellness":      "deep_teal",
    "green":         "deep_teal",
    "sustainability":"deep_teal",
    "environment":   "deep_teal",
    "marketing":     "deep_indigo",
    "creative":      "deep_indigo",
    "design":        "deep_indigo",
    "media":         "deep_indigo",
    "education":     "deep_indigo",
    "agency":        "deep_indigo",
    "branding":      "deep_indigo",
}


# ── Color helpers ─────────────────────────────────────────────────────────────

def _hex_to_rgb(hex_str: str):
    h = hex_str.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    if len(h) != 6:
        return None
    try:
        return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))
    except Exception:
        return None


def _luminance(rgb: tuple) -> float:
    return (0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]) / 255


def _mix(a: tuple, b: tuple, t: float) -> tuple:
    t = max(0.0, min(1.0, t))
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _get_palette(company: dict) -> dict:
    """
    Priority: scraped brand_color → explicit carousel_theme → industry keyword → default.
    """
    # 1. Scraped brand color overrides everything
    brand_hex = company.get("brand_color", "")
    if brand_hex and brand_hex.startswith("#"):
        accent = _hex_to_rgb(brand_hex)
        if accent and 0.10 < _luminance(accent) < 0.92:
            bg = tuple(max(0, min(28, int(c * 0.12))) for c in accent)
            return {
                "bg":       bg,
                "accent":   accent,
                "title":    (255, 255, 255),
                "subtitle": _mix(bg, (255, 255, 255), 0.55),
                "body":     _mix(bg, (255, 255, 255), 0.82),
                "muted":    _mix(bg, (255, 255, 255), 0.28),
            }

    # 2. Explicit theme chosen in profile settings
    theme = company.get("carousel_theme", "")
    if theme and theme in PALETTES:
        return PALETTES[theme]

    # 3. Industry keyword match
    industry = company.get("industry", "").lower()
    for key, palette_name in _INDUSTRY_MAP.items():
        if key in industry:
            return PALETTES[palette_name]

    # 4. Personal brand default → warm dark
    if company.get("profile_type") == "personal":
        return PALETTES["warm_dark"]

    return PALETTES["dark_pro"]


# ── Font helpers ──────────────────────────────────────────────────────────────

_FONTS_DIR = os.path.join(os.path.dirname(__file__), "fonts")

def _font(size: int, bold: bool = False, semi: bool = False) -> ImageFont.FreeTypeFont:
    # Inter first (bundled), then system fallbacks
    candidates = [
        (os.path.join(_FONTS_DIR, "Inter-Bold.ttf"),     True,  False),
        (os.path.join(_FONTS_DIR, "Inter-SemiBold.ttf"), False, True),
        (os.path.join(_FONTS_DIR, "Inter-Regular.ttf"),  False, False),
        ("C:/Windows/Fonts/seguisb.ttf",                 True,  False),
        ("C:/Windows/Fonts/segoeui.ttf",                 False, False),
        ("C:/Windows/Fonts/arialbd.ttf",                 True,  False),
        ("C:/Windows/Fonts/arial.ttf",                   False, False),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", True, False),
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",      False, False),
    ]
    for path, is_bold, is_semi in candidates:
        if not os.path.exists(path):
            continue
        if bold and not is_bold:
            continue
        if semi and not is_semi and not is_bold:
            continue
        try:
            return ImageFont.truetype(path, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _wrap(draw, text, font, max_w):
    words, lines, cur = text.split(), [], []
    for word in words:
        test = " ".join(cur + [word])
        if draw.textbbox((0, 0), test, font=font)[2] > max_w and cur:
            lines.append(" ".join(cur))
            cur = [word]
        else:
            cur.append(word)
    if cur:
        lines.append(" ".join(cur))
    return lines


def _put_text(draw, text, font, x, y, max_w, color, gap=10) -> int:
    h = 0
    for line in _wrap(draw, text, font, max_w):
        draw.text((x, y + h), line, font=font, fill=color)
        h += draw.textbbox((0, 0), line, font=font)[3] + gap
    return h


def _tw(draw, text, font) -> int:
    return draw.textbbox((0, 0), text, font=font)[2]


def _text_block_height(draw, text, font, max_w, gap=10) -> int:
    """Measure total pixel height of wrapped text block (no trailing gap)."""
    lines = _wrap(draw, text, font, max_w)
    if not lines:
        return 0
    total = 0
    for i, line in enumerate(lines):
        lh = draw.textbbox((0, 0), line, font=font)[3]
        total += lh + (gap if i < len(lines) - 1 else 0)
    return total


# ── Unified dark template ─────────────────────────────────────────────────────
# All three slide types share the same dark bg. Only bg+accent change per brand.

def _slide_hook(headline: str, subtext: str, num: int, total: int,
                p: dict) -> Image.Image:
    img  = Image.new("RGB", (_RW, _RH), p["bg"])
    draw = ImageDraw.Draw(img)

    f_head = _font(116 * _SCALE, bold=True)
    f_sub  = _font(36  * _SCALE)
    f_sm   = _font(22  * _SCALE)

    max_w = _RW - PAD * 2
    h_head = _text_block_height(draw, headline, f_head, max_w, gap=20 * _SCALE)
    h_sub  = _text_block_height(draw, subtext[:120], f_sub, max_w, gap=11 * _SCALE)
    block_h = 5 * _SCALE + 14 * _SCALE + h_head + 20 * _SCALE + 32 * _SCALE + h_sub

    y = (_RH - block_h) // 2

    draw.rectangle([PAD, y, PAD + 56 * _SCALE, y + 5 * _SCALE], fill=p["accent"])
    y += 14 * _SCALE
    y += _put_text(draw, headline, f_head, PAD, y, max_w, p["title"], gap=20 * _SCALE)
    y += 32 * _SCALE
    _put_text(draw, subtext[:120], f_sub, PAD, y, max_w, p["subtitle"], gap=11 * _SCALE)

    draw.text((PAD, _RH - 62 * _SCALE), f"{num}/{total}", font=f_sm, fill=p["muted"])
    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


def _slide_content(title: str, body: str, num: int, total: int,
                   brand: str, p: dict) -> Image.Image:
    # Cap body to 2 punchy sentences
    body = body[:180].rsplit(" ", 1)[0] if len(body) > 180 else body
    bg2  = _mix(p["bg"], (255, 255, 255), 0.035)

    img  = Image.new("RGB", (_RW, _RH), bg2)
    draw = ImageDraw.Draw(img)

    point_num = str(num - 1).zfill(2)
    f_num   = _font(140 * _SCALE, bold=True)   # slightly smaller so number breathes
    f_title = _font(68  * _SCALE, bold=True)
    f_body  = _font(34  * _SCALE)
    f_sm    = _font(22  * _SCALE)

    max_w = _RW - PAD * 2

    # Top accent bar
    y = PAD
    draw.rectangle([PAD, y, PAD + 48 * _SCALE, y + 5 * _SCALE], fill=p["accent"])
    y += 28 * _SCALE

    # Large faded number
    num_color = _mix(bg2, p["accent"], 0.25)
    draw.text((PAD, y), point_num, font=f_num, fill=num_color)
    num_h = draw.textbbox((0, 0), point_num, font=f_num)[3]
    y += num_h + 28 * _SCALE          # ← increased gap: number → divider

    # Divider line
    draw.rectangle([PAD, y, PAD + 56 * _SCALE, y + 4 * _SCALE], fill=p["accent"])
    y += 32 * _SCALE                  # ← increased gap: divider → title

    # Point title
    y += _put_text(draw, title, f_title, PAD, y, max_w, p["title"], gap=12 * _SCALE)
    y += 24 * _SCALE

    # Body text
    _put_text(draw, body, f_body, PAD, y, max_w, p["body"], gap=14 * _SCALE)

    # Footer
    draw.text((PAD, _RH - 62 * _SCALE), f"{num}/{total}", font=f_sm, fill=p["muted"])
    bw = _tw(draw, brand, f_sm)
    draw.text((_RW - PAD - bw, _RH - 62 * _SCALE), brand, font=f_sm, fill=p["muted"])
    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


def _slide_cta(headline: str, cta: str, num: int, total: int,
               brand: str, p: dict) -> Image.Image:
    img  = Image.new("RGB", (_RW, _RH), p["bg"])
    draw = ImageDraw.Draw(img)

    f_head = _font(82 * _SCALE, bold=True)
    f_cta  = _font(46 * _SCALE)
    f_sm   = _font(22 * _SCALE)

    max_w = _RW - PAD * 2
    h_head = _text_block_height(draw, headline, f_head, max_w, gap=14 * _SCALE)
    h_cta  = _text_block_height(draw, cta,      f_cta,  max_w, gap=10 * _SCALE)
    block_h = 5 * _SCALE + 14 * _SCALE + h_head + 14 * _SCALE + 28 * _SCALE + h_cta

    y = (_RH - block_h) // 2

    draw.rectangle([PAD, y, PAD + 56 * _SCALE, y + 5 * _SCALE], fill=p["accent"])
    y += 14 * _SCALE
    h = _put_text(draw, headline, f_head, PAD, y, max_w, p["title"], gap=14 * _SCALE)
    _put_text(draw, cta, f_cta, PAD, y + h + 28 * _SCALE, max_w, p["accent"], gap=10 * _SCALE)

    draw.text((PAD, _RH - 62 * _SCALE), brand, font=f_sm, fill=p["accent"])
    draw.text((_RW - PAD - 72 * _SCALE, _RH - 62 * _SCALE), f"{num}/{total}", font=f_sm, fill=p["muted"])
    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


# ── Manual carousel (from pasted content) ────────────────────────────────────

def generate_carousel_from_text(raw_text: str) -> dict:
    prompt = f"""Turn the following content into a 5-slide LinkedIn carousel.

Content:
{raw_text[:2000]}

QUALITY RULES:
- hook_slide.headline: The single sharpest insight from the content — max 8 words. Must be specific, not generic.
- hook_slide.subtext: One punchy supporting fact or stat from the content — max 12 words.
- Each content slide: 2-3 specific sentences using real data, names, or numbers from the content.
- cta_slide.headline: The one takeaway someone should remember — max 8 words.
- cta_slide.cta: A specific question that invites the reader to engage — max 15 words.
- post_text: Hook line first (no warm-up). 2-3 sentences. Ends with 3 lowercase hashtags on own line.
- NEVER use: "game-changer", "landscape", "unlock", "dive deep", "revolutionize", "in today's world".

Return ONLY valid JSON:
{{
  "hook_slide": {{"headline": "...", "subtext": "..."}},
  "content_slides": [
    {{"title": "The point in max 6 words", "body": "1-2 sentences max. Specific, punchy."}},
    {{"title": "The point in max 6 words", "body": "1-2 sentences max. Specific, punchy."}},
    {{"title": "The point in max 6 words", "body": "1-2 sentences max. Specific, punchy."}}
  ],
  "cta_slide": {{"headline": "...", "cta": "..."}},
  "post_text": "Hook line.\\n\\nOne supporting line.\\n\\nOne closing line.\\n\\n#tag1 #tag2 #tag3"
}}"""

    res = _groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "You are a LinkedIn carousel content generator. Return ONLY valid JSON matching the schema exactly. Each content slide title is the point itself (bold, direct). Each body is MAX 2 short sentences — specific and punchy, no fluff."},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=1024,
    )
    return json.loads(res.choices[0].message.content)


# ── Autonomous content generation ────────────────────────────────────────────

def generate_carousel_content(company: dict, news_context: str, post_type: str) -> dict:
    from autonomous import _build_company_brief, POST_TYPE_LABELS

    is_personal = company.get("profile_type") == "personal"
    voice_note  = "First person (I, my, I've) — personal brand voice." if is_personal else "Third person — company voice."

    prompt = f"""Generate a 5-slide LinkedIn carousel for {company['name']} ({company['industry']}).

Post type: {POST_TYPE_LABELS.get(post_type, post_type)}
News/context: {news_context or "Draw from general industry knowledge."}
Author context: {_build_company_brief(company)[:800]}

QUALITY RULES — non-negotiable:
- hook_slide.headline: Must be a SPECIFIC, surprising, or contrarian claim. NOT "X Changes Everything" or "X Is Important". Good examples: "45% of enterprise tasks will be AI-automated by 2027", "Most AI agents fail in week 2 — here's why", "Salesforce just cut 1,000 jobs. AI did it in 3 months."
- hook_slide.subtext: One punchy line that earns the swipe. Must add a specific fact, tension, or intrigue.
- content_slides[*].body: Name real companies, real reports, real percentages. No vague claims. Each sentence must be specific enough that a reader could Google it.
- cta_slide.cta: A sharp, specific question aimed at this exact audience — not "what do you think?"
- post_text: Hook line first (no warm-up). No filler. Ends with 3 lowercase hashtags on their own line.
- NEVER use: "game-changer", "landscape", "unlock", "dive deep", "revolutionize", "in today's world", "I'm excited to see", "I see huge potential", "the future is here", "as X continues to advance", "are you ready", "this is just the beginning".
- {voice_note}

Return ONLY valid JSON — no markdown, no extra text:
{{
  "hook_slide": {{
    "headline": "Specific bold claim — max 8 words",
    "subtext": "Specific tension or fact that earns the swipe — max 12 words"
  }},
  "content_slides": [
    {{"title": "The point in max 6 words", "body": "1-2 sentences. Specific stat, name, or example. No fluff."}},
    {{"title": "The point in max 6 words", "body": "1-2 sentences. Specific stat, name, or example. No fluff."}},
    {{"title": "The point in max 6 words", "body": "1-2 sentences. Specific stat, name, or example. No fluff."}}
  ],
  "cta_slide": {{
    "headline": "Sharp one-line takeaway — max 8 words",
    "cta": "Specific question for this exact audience — max 15 words"
  }},
  "post_text": "Hook line.\\n\\nOne supporting line.\\n\\nOne closing line.\\n\\n#tag1 #tag2 #tag3"
}}"""

    res = _groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": "You are a LinkedIn carousel content generator. Return ONLY valid JSON — no markdown, no explanation. CRITICAL: each content_slide title is the core point (direct, bold, max 6 words). Each body is MAX 2 short sentences with real specifics — no vague claims, no padding."},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=1024,
    )
    return json.loads(res.choices[0].message.content)


# ── PDF assembly ──────────────────────────────────────────────────────────────

def render_carousel_pdf(content: dict, company: dict) -> bytes:
    brand    = company.get("name", "Voyce")
    hook     = content["hook_slide"]
    c_slides = content.get("content_slides", [])
    cta      = content["cta_slide"]
    total    = 1 + len(c_slides) + 1

    p = _get_palette(company)

    slides = [_slide_hook(hook["headline"], hook["subtext"], 1, total, p)]
    for i, s in enumerate(c_slides):
        slides.append(_slide_content(s["title"], s["body"], 2 + i, total, brand, p))
    slides.append(_slide_cta(cta["headline"], cta["cta"], total, total, brand, p))

    buf = io.BytesIO()
    slides[0].save(buf, format="PDF", save_all=True, append_images=slides[1:], resolution=300)
    return buf.getvalue()
