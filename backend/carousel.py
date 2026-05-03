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


# ── Visual helper utilities ───────────────────────────────────────────────────

def _draw_gradient_bg(img: Image.Image, top_color: tuple, bot_color: tuple):
    """Vertical gradient fill over the full image in-place."""
    w, h = img.size
    pixels = img.load()
    for y in range(h):
        t = y / max(h - 1, 1)
        r = int(top_color[0] + (bot_color[0] - top_color[0]) * t)
        g = int(top_color[1] + (bot_color[1] - top_color[1]) * t)
        b = int(top_color[2] + (bot_color[2] - top_color[2]) * t)
        for x in range(w):
            pixels[x, y] = (r, g, b)


def _draw_circle(draw: ImageDraw.Draw, cx: int, cy: int, r: int, color: tuple):
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=color)


def _draw_rounded_rect(draw: ImageDraw.Draw, x0: int, y0: int, x1: int, y1: int,
                       radius: int, fill: tuple):
    draw.rounded_rectangle([x0, y0, x1, y1], radius=radius, fill=fill)


def _draw_dot_grid(draw: ImageDraw.Draw, x0: int, y0: int, cols: int, rows: int,
                   spacing: int, dot_r: int, color: tuple):
    """Decorative dot-grid pattern in a rectangular region."""
    for row in range(rows):
        for col in range(cols):
            cx = x0 + col * spacing
            cy = y0 + row * spacing
            _draw_circle(draw, cx, cy, dot_r, color)


def _draw_corner_arc(draw: ImageDraw.Draw, cx: int, cy: int,
                     r_outer: int, r_inner: int, color: tuple):
    """Concentric quarter-circle arcs for decorative corner element."""
    for r in range(r_inner, r_outer, max(1, (r_outer - r_inner) // 4)):
        draw.arc([cx - r, cy - r, cx + r, cy + r], start=180, end=270, fill=color,
                 width=max(2, (r_outer - r_inner) // 6))


# ── Premium slide renderers ───────────────────────────────────────────────────

def _slide_hook(headline: str, subtext: str, num: int, total: int,
                p: dict) -> Image.Image:
    """Hook slide: diagonal gradient + geometric circle + bold headline + pill counter."""
    # Gradient: bg → slightly accent-tinted
    top_c = p["bg"]
    bot_c = _mix(p["bg"], p["accent"], 0.18)
    img = Image.new("RGB", (_RW, _RH), top_c)
    _draw_gradient_bg(img, top_c, bot_c)
    draw = ImageDraw.Draw(img)

    # ── Decorative large ghost circle top-right ──
    ghost = _mix(p["bg"], p["accent"], 0.12)
    cr = int(520 * _SCALE)
    _draw_circle(draw, _RW - int(160 * _SCALE), -int(160 * _SCALE), cr, ghost)
    # Smaller brighter circle inside
    ghost2 = _mix(p["bg"], p["accent"], 0.22)
    _draw_circle(draw, _RW - int(160 * _SCALE), -int(160 * _SCALE), int(cr * 0.55), ghost2)

    # ── Full-height left accent bar ──
    bar_w = int(10 * _SCALE)
    margin = int(52 * _SCALE)
    draw.rectangle([margin, margin, margin + bar_w, _RH - margin], fill=p["accent"])

    # ── Content block (vertically centered, offset right of bar) ──
    f_head = _font(112 * _SCALE, bold=True)
    f_sub  = _font(38  * _SCALE)
    f_sm   = _font(21  * _SCALE)
    text_x = margin + bar_w + int(40 * _SCALE)
    max_w  = _RW - text_x - int(80 * _SCALE)

    h_head = _text_block_height(draw, headline, f_head, max_w, gap=18 * _SCALE)
    h_sub  = _text_block_height(draw, subtext[:130], f_sub, max_w, gap=10 * _SCALE)
    divider_h = int(5 * _SCALE)
    gap_after_head = int(28 * _SCALE)
    gap_before_sub = int(24 * _SCALE)
    block_h = h_head + gap_after_head + divider_h + gap_before_sub + h_sub
    y = (_RH - block_h) // 2

    # Headline
    y += _put_text(draw, headline, f_head, text_x, y, max_w, p["title"], gap=18 * _SCALE)
    y += gap_after_head

    # Accent divider line (wider)
    draw.rectangle([text_x, y, text_x + int(72 * _SCALE), y + divider_h], fill=p["accent"])
    y += divider_h + gap_before_sub

    # Subtext
    _put_text(draw, subtext[:130], f_sub, text_x, y, max_w, p["subtitle"], gap=10 * _SCALE)

    # ── Dot grid decoration bottom-right ──
    dot_color = _mix(p["bg"], p["accent"], 0.20)
    _draw_dot_grid(draw, _RW - int(260 * _SCALE), _RH - int(260 * _SCALE),
                   5, 5, int(44 * _SCALE), int(5 * _SCALE), dot_color)

    # ── Pill counter bottom-left ──
    pill_text = f"{num} / {total}"
    pill_w = _tw(draw, pill_text, f_sm) + int(32 * _SCALE)
    pill_h = int(36 * _SCALE)
    px = margin + bar_w + int(40 * _SCALE)
    py = _RH - margin - pill_h
    pill_bg = _mix(p["bg"], p["accent"], 0.30)
    _draw_rounded_rect(draw, px, py, px + pill_w, py + pill_h, int(18 * _SCALE), pill_bg)
    draw.text((px + int(16 * _SCALE), py + int(7 * _SCALE)), pill_text, font=f_sm, fill=p["title"])

    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


def _slide_content(title: str, body: str, num: int, total: int,
                   brand: str, p: dict) -> Image.Image:
    """Content slide: split layout — giant clipped number left, accent panel right."""
    body = body[:200].rsplit(" ", 1)[0] if len(body) > 200 else body

    # Slightly lighter background
    bg2 = _mix(p["bg"], (255, 255, 255), 0.04)
    img = Image.new("RGB", (_RW, _RH), bg2)
    draw = ImageDraw.Draw(img)

    # ── Right-side accent panel (subtle) ──
    panel_x = int(_RW * 0.62)
    panel_color = _mix(bg2, p["accent"], 0.08)
    draw.rectangle([panel_x, 0, _RW, _RH], fill=panel_color)

    # ── Top accent bar (full width) ──
    draw.rectangle([0, 0, _RW, int(8 * _SCALE)], fill=p["accent"])

    # ── Giant background number (left half, very faded, clipped feel) ──
    f_giant = _font(320 * _SCALE, bold=True)
    point_num = str(num - 1).zfill(2)
    ghost_num_color = _mix(bg2, p["accent"], 0.14)
    num_bbox = draw.textbbox((0, 0), point_num, font=f_giant)
    num_w = num_bbox[2]
    # Position so number is partially off the left edge for dynamic cropped feel
    num_x = PAD - int(num_w * 0.05)
    num_y = int(_RH * 0.10)
    draw.text((num_x, num_y), point_num, font=f_giant, fill=ghost_num_color)

    # ── Content in lower-center area ──
    f_label = _font(22 * _SCALE, bold=True)
    f_title = _font(72 * _SCALE, bold=True)
    f_sm    = _font(21 * _SCALE)

    content_x = PAD
    max_w = panel_x - PAD - int(40 * _SCALE)
    content_y = int(_RH * 0.50)

    # Small step label above title
    label_text = f"STEP {num - 1}"
    label_color = p["accent"]
    draw.text((content_x, content_y), label_text, font=f_label, fill=label_color)
    content_y += int(34 * _SCALE)

    # Short accent underline
    draw.rectangle([content_x, content_y, content_x + int(50 * _SCALE),
                    content_y + int(4 * _SCALE)], fill=p["accent"])
    content_y += int(22 * _SCALE)

    # Title
    content_y += _put_text(draw, title, f_title, content_x, content_y,
                           max_w, p["title"], gap=10 * _SCALE)
    content_y += int(20 * _SCALE)

    # Body in a softly tinted box, auto-fit to available vertical space
    footer_y = _RH - int(70 * _SCALE)
    box_pad = int(20 * _SCALE)
    body_gap = int(12 * _SCALE)
    available_h = max(0, footer_y - content_y - box_pad - int(22 * _SCALE))
    f_body = _font(34 * _SCALE)
    fitted_body = body
    max_lines = 6
    for size in (34, 32, 30, 28, 26):
        f_try = _font(size * _SCALE)
        text = body
        if len(text) > 170:
            text = text[:170].rsplit(" ", 1)[0]
        lines = _wrap(draw, text, f_try, max_w)[:max_lines]
        if len(lines) == max_lines and len(_wrap(draw, text, f_try, max_w)) > max_lines:
            lines[-1] = lines[-1].rstrip(" .,;:") + "..."
        text_fit = " ".join(lines)
        h = _text_block_height(draw, text_fit, f_try, max_w, gap=body_gap)
        if h <= available_h:
            f_body = f_try
            fitted_body = text_fit
            break
    body_lines_h = _text_block_height(draw, fitted_body, f_body, max_w, gap=body_gap)
    box_pad = int(20 * _SCALE)
    box_bg = _mix(bg2, p["accent"], 0.10)
    _draw_rounded_rect(draw, content_x - box_pad,
                       content_y - box_pad,
                       content_x + max_w + box_pad,
                       content_y + body_lines_h + box_pad,
                       int(12 * _SCALE), box_bg)
    _put_text(draw, fitted_body, f_body, content_x, content_y, max_w, p["body"], gap=body_gap)

    # ── Right panel decoration: corner arc ──
    arc_color = _mix(panel_color, p["accent"], 0.25)
    _draw_corner_arc(draw, _RW, 0, int(340 * _SCALE), int(180 * _SCALE), arc_color)

    # ── Footer: thin full-width divider + brand left / counter right ──
    draw.rectangle([PAD, footer_y, _RW - PAD, footer_y + int(1 * _SCALE)],
                   fill=p["muted"])
    footer_text_y = footer_y + int(14 * _SCALE)
    draw.text((PAD, footer_text_y), brand, font=f_sm, fill=p["muted"])
    counter = f"{num}/{total}"
    cw = _tw(draw, counter, f_sm)
    draw.text((_RW - PAD - cw, footer_text_y), counter, font=f_sm, fill=p["muted"])

    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


def _slide_cta(headline: str, cta: str, num: int, total: int,
               brand: str, p: dict) -> Image.Image:
    """CTA slide: split bg + centred bold headline + CTA in accent pill + dot deco."""
    # Split background: dark top, accent-tinted bottom
    split_y = int(_RH * 0.58)
    top_c = p["bg"]
    bot_c = _mix(p["bg"], p["accent"], 0.22)
    img = Image.new("RGB", (_RW, _RH), top_c)
    _draw_gradient_bg(img, top_c, bot_c)
    draw = ImageDraw.Draw(img)

    # ── Large ghost circle top-left ──
    ghost = _mix(p["bg"], p["accent"], 0.10)
    _draw_circle(draw, -int(80 * _SCALE), -int(80 * _SCALE), int(480 * _SCALE), ghost)

    # ── Dot grid top-right ──
    dot_col = _mix(p["bg"], p["accent"], 0.18)
    _draw_dot_grid(draw, _RW - int(280 * _SCALE), int(60 * _SCALE),
                   5, 4, int(48 * _SCALE), int(5 * _SCALE), dot_col)

    # ── Full-width thin top accent bar ──
    draw.rectangle([0, 0, _RW, int(8 * _SCALE)], fill=p["accent"])

    f_head = _font(90 * _SCALE, bold=True)
    f_cta  = _font(40 * _SCALE)
    f_sm   = _font(21 * _SCALE)
    max_w  = _RW - PAD * 2

    h_head = _text_block_height(draw, headline, f_head, max_w, gap=14 * _SCALE)
    h_cta  = _text_block_height(draw, cta, f_cta, max_w - int(80 * _SCALE), gap=10 * _SCALE)

    pill_v_pad = int(24 * _SCALE)
    pill_h_pad = int(36 * _SCALE)
    pill_height = h_cta + pill_v_pad * 2

    total_h = h_head + int(48 * _SCALE) + pill_height
    y = (_RH - total_h) // 2

    # Headline (centred)
    lines = _wrap(draw, headline, f_head, max_w)
    for line in lines:
        lw = _tw(draw, line, f_head)
        draw.text(((_RW - lw) // 2, y), line, font=f_head, fill=p["title"])
        y += draw.textbbox((0, 0), line, font=f_head)[3] + int(14 * _SCALE)
    y += int(48 * _SCALE)

    # CTA in rounded accent pill (centred)
    cta_max_w = max_w - int(80 * _SCALE)
    cta_lines = _wrap(draw, cta, f_cta, cta_max_w)
    pill_inner_h = sum(draw.textbbox((0, 0), l, font=f_cta)[3] + int(10 * _SCALE)
                       for l in cta_lines)
    pill_inner_w = max(_tw(draw, l, f_cta) for l in cta_lines) if cta_lines else int(200 * _SCALE)
    pill_total_w = min(pill_inner_w + pill_h_pad * 2, max_w)
    pill_total_h = pill_inner_h + pill_v_pad * 2
    pill_x = (_RW - pill_total_w) // 2
    pill_bg = p["accent"]
    _draw_rounded_rect(draw, pill_x, y, pill_x + pill_total_w,
                       y + pill_total_h, int(pill_total_h // 2), pill_bg)
    ty = y + pill_v_pad
    for line in cta_lines:
        lw = _tw(draw, line, f_cta)
        tx = (_RW - lw) // 2
        draw.text((tx, ty), line, font=f_cta, fill=p["bg"])
        ty += draw.textbbox((0, 0), line, font=f_cta)[3] + int(10 * _SCALE)

    # ── Brand bottom-centre in accent color ──
    bw = _tw(draw, brand, f_sm)
    draw.text(((_RW - bw) // 2, _RH - int(72 * _SCALE)), brand, font=f_sm, fill=p["accent"])

    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


# ── Manual carousel (from pasted content) ────────────────────────────────────

_CAROUSEL_SYSTEM = """You are a world-class LinkedIn carousel strategist who has studied 10,000+ viral posts.

Your job: extract the most SURPRISING, COUNTERINTUITIVE, or CONTROVERSIAL insight from any content and build a carousel that stops the scroll.

HOOK SLIDE RULES (most important):
The headline must use ONE of these proven scroll-stopping formulas:
  - The Specific Number: "43% of AI projects fail at deployment. Not ideation."
  - The Counterintuitive Truth: "The best engineers I know break every best practice."
  - The Named Event: "Salesforce cut 1,000 jobs. AI did it in 90 days."
  - The Uncomfortable Confession: "I built the wrong product for 6 months. Here's what I missed."
  - The Myth-Buster: "Everyone says X. The data says the opposite."
NEVER write: "X Is Important", "X Changes Everything", "The Future of X", "Why X Matters", "Top X Ways to..."

CONTENT SLIDE RULES:
- Title = the point itself, stated as a bold claim (not a question, not vague)
- Body = name a real company, a real report, or a real number. If none in content, use a well-known industry example.
- Every body sentence must be specific enough that someone could verify it online.

CTA SLIDE RULES:
- Headline = the ONE thing you want them to remember — state it as a bold declaration
- CTA = a sharp question that only THIS specific audience would care about. Not "what do you think?"

Banned words/phrases: game-changer, landscape, unlock, dive deep, revolutionize, leverage, synergy,
in today's world, the future is here, are you ready, this is just the beginning, I'm excited,
it goes without saying, at the end of the day, paradigm shift, move the needle.

Return ONLY valid JSON."""


def generate_carousel_from_text(raw_text: str, company: dict = None) -> dict:
    # Phase 0: generate a locked scroll-stopping hook headline first
    locked_headline = ""
    try:
        from hooks import generate_hook
        allowed = company.get("allowed_hooks", []) if company else []
        locked_headline = generate_hook(context=raw_text[:1200], industry="general", allowed_hooks=allowed)
    except Exception:
        pass

    hook_constraint = (
        f"\nCRITICAL: hook_slide.headline MUST be exactly: \"{locked_headline}\" (do not change it)."
        if locked_headline else ""
    )

    prompt = f"""Content to turn into a 5-slide LinkedIn carousel:

{raw_text[:2000]}

Extract the single most surprising or counterintuitive insight. Build the carousel around that.

Slide 1 (hook): One scroll-stopping headline (max 8 words) + one punchy tension line (max 12 words).
Slides 2-4 (content): Each gets a bold title (max 6 words, stated as a claim) + 1-2 sentences with real specifics.
Slide 5 (CTA): A bold takeaway declaration (max 8 words) + a sharp question for this specific audience (max 15 words).
post_text: Hook first. No warm-up. 3-4 short paragraphs. Ends with 3 relevant lowercase hashtags on their own line.
{hook_constraint}

Return ONLY valid JSON:
{{
  "hook_slide": {{"headline": "...", "subtext": "..."}},
  "content_slides": [
    {{"title": "Bold claim max 6 words", "body": "Specific fact, name, or number. Max 2 sentences."}},
    {{"title": "Bold claim max 6 words", "body": "Specific fact, name, or number. Max 2 sentences."}},
    {{"title": "Bold claim max 6 words", "body": "Specific fact, name, or number. Max 2 sentences."}}
  ],
  "cta_slide": {{"headline": "Bold declaration max 8 words", "cta": "Sharp specific question max 15 words"}},
  "post_text": "Hook line.\\n\\nSupporting point.\\n\\nClosing insight.\\n\\n#tag1 #tag2 #tag3"
}}"""

    res = _groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": _CAROUSEL_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=1200,
    )
    result = json.loads(res.choices[0].message.content)
    # Enforce locked headline
    if locked_headline and result.get("hook_slide"):
        result["hook_slide"]["headline"] = locked_headline
    return result


# ── Autonomous content generation ────────────────────────────────────────────

def generate_carousel_content(company: dict, news_context: str, post_type: str) -> dict:
    from autonomous import _build_company_brief, POST_TYPE_LABELS

    is_personal = company.get("profile_type") == "personal"
    voice_note = "First person (I, my, I've) — write as if the author is speaking directly." if is_personal else "Third person — company voice, authoritative."
    company_brief = _build_company_brief(company)[:900]

    # Phase 0: generate a locked hook headline using the hook engine
    locked_headline = ""
    try:
        from hooks import generate_hook
        context = (news_context or "") + "\n\n" + company_brief[:500]
        allowed = company.get("allowed_hooks", [])
        locked_headline = generate_hook(context=context, industry=company["industry"], post_type=post_type, allowed_hooks=allowed)
    except Exception:
        pass

    hook_formula_map = {
        "trend_commentary":  "Named Event: 'Company/Report just did X. The implication no one is talking about: Y.'",
        "trend_reaction":    "Named Event: 'Company/Report just did X. The implication no one is talking about: Y.'",
        "industry_stat":     "Specific Number: '[Exact %/number] of [audience] [surprising fact]. Not [assumed cause]. [Real cause].'",
        "stat_reaction":     "Specific Number: '[Exact %/number] of [audience] [surprising fact]. Not [assumed cause]. [Real cause].'",
        "expert_insight":    "Counterintuitive Truth: 'The [best/top/smartest] [people/companies] in [industry] [do the opposite of conventional wisdom].'",
        "expert_insight_p":  "Counterintuitive Truth: 'The [best/top/smartest] [people/companies] in [industry] [do the opposite of conventional wisdom].'",
        "hot_take":          "Myth-Buster: 'Everyone says [X]. The data/reality says the opposite.'",
        "lesson_learned":    "Uncomfortable Confession: 'I [made specific mistake] for [timeframe]. Here is what I missed.'",
        "personal_story":    "Scene Drop: Drop into a specific moment. Name the situation, the stakes, the turning point.",
        "product_spotlight": "Problem-First: '[Specific pain point] costs [industry] companies [estimate] per year. Most teams are fixing the wrong thing.'",
        "case_study":        "Result-First: '[Specific outcome] in [timeframe]. Here is exactly how it happened.'",
    }
    hook_formula = hook_formula_map.get(post_type, "Counterintuitive Truth: state the surprising reality, not the obvious take.")

    hook_constraint = (
        f"\nCRITICAL: hook_slide.headline MUST be exactly: \"{locked_headline}\" (do not change it)."
        if locked_headline else ""
    )

    prompt = f"""Generate a 5-slide LinkedIn carousel for {company['name']} ({company['industry']}).

Post type: {POST_TYPE_LABELS.get(post_type, post_type)}
Latest industry context: {news_context or 'Draw from well-known industry examples and published reports.'}
Author/company context: {company_brief}

HOOK SLIDE — use this formula for the subtext (headline is already set):
{hook_formula}
The subtext must add a specific fact, number, or tension — not just restate the headline.

CONTENT SLIDES (3 slides):
- Title: the point stated as a bold declarative claim (max 6 words)
- Body: name real companies, cite real reports/numbers, or give a specific verifiable example
- No slide can say something generic that would apply to any industry

CTA SLIDE:
- Headline: the single most memorable takeaway — stated as a bold declaration
- CTA: a sharp question only a {company['industry']} professional would deeply care about

post_text: Start with the hook — no warm-up sentence. Write 3 short punchy paragraphs. Close with a question. End with 3 lowercase hashtags on their own line.

{voice_note}
{hook_constraint}

Return ONLY valid JSON:
{{
  "hook_slide": {{"headline": "...", "subtext": "..."}},
  "content_slides": [
    {{"title": "...", "body": "..."}},
    {{"title": "...", "body": "..."}},
    {{"title": "...", "body": "..."}}
  ],
  "cta_slide": {{"headline": "...", "cta": "..."}},
  "post_text": "..."
}}"""

    res = _groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": _CAROUSEL_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        response_format={"type": "json_object"},
        max_tokens=1200,
    )
    result = json.loads(res.choices[0].message.content)
    # Enforce locked headline
    if locked_headline and result.get("hook_slide"):
        result["hook_slide"]["headline"] = locked_headline
    return result


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
