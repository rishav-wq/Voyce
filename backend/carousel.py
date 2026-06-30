import io
import os
import re

from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

from llm import generate_json

load_dotenv()

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
    "clean_light": {
        "bg":       (250, 250, 252),   # #fafafc — near-white
        "accent":   (79,  70,  229),   # #4f46e5 indigo
        "title":    (17,  24,  39),    # #111827
        "subtitle": (75,  85,  99),    # #4b5563
        "body":     (55,  65,  81),    # #374151
        "muted":    (180, 184, 196),   # light gray
    },
    "warm_paper": {
        "bg":       (252, 248, 240),   # #fcf8f0 — warm cream
        "accent":   (194, 65,  12),    # #c2410c burnt orange
        "title":    (41,  37,  36),    # #292524
        "subtitle": (87,  83,  78),    # #57534e
        "body":     (68,  64,  60),    # #44403c
        "muted":    (196, 190, 178),   # warm gray
    },
    "electric": {
        "bg":       (9,   9,   11),    # #09090b — near-black
        "accent":   (163, 230, 53),    # #a3e635 lime
        "title":    (255, 255, 255),
        "subtitle": (161, 161, 170),   # #a1a1aa
        "body":     (212, 212, 216),   # #d4d4d8
        "muted":    (63,  63,  70),    # #3f3f46
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


def _fit_text_to_box(draw, text: str, max_w: int, max_h: int, sizes: list[int], gap: int,
                     max_lines: int | None = None):
    """
    Fit text into a width/height box by stepping down font size and optionally line count.
    Returns (fitted_text, font, height).
    """
    clean = " ".join((text or "").split())
    if not clean:
        f = _font(sizes[-1] * _SCALE, bold=True)
        return "", f, 0

    for size in sizes:
        f_try = _font(size * _SCALE, bold=True)
        lines = _wrap(draw, clean, f_try, max_w)
        if max_lines and len(lines) > max_lines:
            lines = lines[:max_lines]
            lines[-1] = lines[-1].rstrip(" .,;:") + "..."
        candidate = " ".join(lines)
        h = _text_block_height(draw, candidate, f_try, max_w, gap=gap)
        if h <= max_h:
            return candidate, f_try, h

    # Last-resort fallback with smallest size + aggressive trim
    f_last = _font(sizes[-1] * _SCALE, bold=True)
    words = clean.split()
    best = ""
    for i in range(1, len(words) + 1):
        trial = " ".join(words[:i])
        if i < len(words):
            trial = trial.rstrip(" .,;:") + "..."
        h = _text_block_height(draw, trial, f_last, max_w, gap=gap)
        if h > max_h:
            break
        best = trial
    final = best or clean[:60]
    return final, f_last, _text_block_height(draw, final, f_last, max_w, gap=gap)


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
    f_sub  = _font(34 * _SCALE)
    f_sm   = _font(21  * _SCALE)
    text_x = margin + bar_w + int(40 * _SCALE)
    max_w  = _RW - text_x - int(80 * _SCALE)
    footer_reserved = int(180 * _SCALE)
    safe_h = _RH - (margin * 2) - footer_reserved
    head_gap = int(16 * _SCALE)
    sub_gap = int(9 * _SCALE)
    divider_h = int(5 * _SCALE)
    gap_after_head = int(22 * _SCALE)
    gap_before_sub = int(18 * _SCALE)

    hook_text, f_head, h_head = _fit_text_to_box(
        draw, headline, max_w, int(safe_h * 0.65), [112, 104, 96, 88, 80, 72], gap=head_gap, max_lines=6
    )
    sub_text, f_sub, h_sub = _fit_text_to_box(
        draw, subtext[:180], max_w, int(safe_h * 0.30), [38, 36, 34, 32, 30, 28], gap=sub_gap, max_lines=4
    )
    block_h = h_head + gap_after_head + divider_h + gap_before_sub + h_sub
    y = max(margin, (_RH - footer_reserved - block_h) // 2)

    # Headline
    y += _put_text(draw, hook_text, f_head, text_x, y, max_w, p["title"], gap=head_gap)
    y += gap_after_head

    # Accent divider line (wider)
    draw.rectangle([text_x, y, text_x + int(72 * _SCALE), y + divider_h], fill=p["accent"])
    y += divider_h + gap_before_sub

    # Subtext
    _put_text(draw, sub_text, f_sub, text_x, y, max_w, p["subtitle"], gap=sub_gap)

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
                   brand: str, p: dict, label: str = "") -> Image.Image:
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
    f_sm    = _font(21 * _SCALE)

    content_x = PAD
    max_w = panel_x - PAD - int(40 * _SCALE)
    content_y = int(_RH * 0.50)

    # Small label above title (e.g. STEP 1 / MYTH / TRUTH)
    label_text = (label or f"STEP {num - 1}").upper()[:24]
    label_color = p["accent"]
    draw.text((content_x, content_y), label_text, font=f_label, fill=label_color)
    content_y += int(34 * _SCALE)

    # Short accent underline
    draw.rectangle([content_x, content_y, content_x + int(50 * _SCALE),
                    content_y + int(4 * _SCALE)], fill=p["accent"])
    content_y += int(22 * _SCALE)

    # Title (fit safely before body)
    footer_y = _RH - int(70 * _SCALE)
    max_title_h = int((_RH * 0.22))
    title_text, f_title, title_h = _fit_text_to_box(
        draw, title, max_w, max_title_h, [72, 66, 60, 54, 48], gap=int(10 * _SCALE), max_lines=4
    )
    content_y += _put_text(draw, title_text, f_title, content_x, content_y,
                           max_w, p["title"], gap=10 * _SCALE)
    content_y += int(16 * _SCALE)

    # Body in a softly tinted box, auto-fit to available vertical space
    box_pad = int(20 * _SCALE)
    body_gap = int(12 * _SCALE)
    available_h = max(0, footer_y - content_y - box_pad - int(22 * _SCALE))
    body_source = body[:220].rsplit(" ", 1)[0] if len(body) > 220 else body
    fitted_body, f_body, _ = _fit_text_to_box(
        draw, body_source, max_w, available_h, [34, 32, 30, 28, 26, 24], gap=body_gap, max_lines=7
    )
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


def _slide_stat(stat: str, title: str, body: str, num: int, total: int,
                brand: str, p: dict) -> Image.Image:
    """Stat slide: one huge number centered with a caption — maximum visual punch."""
    top_c = p["bg"]
    bot_c = _mix(p["bg"], p["accent"], 0.14)
    img = Image.new("RGB", (_RW, _RH), top_c)
    _draw_gradient_bg(img, top_c, bot_c)
    draw = ImageDraw.Draw(img)

    # Decorations: ghost circle + dot grid
    _draw_circle(draw, -int(120 * _SCALE), _RH - int(60 * _SCALE),
                 int(420 * _SCALE), _mix(p["bg"], p["accent"], 0.10))
    _draw_dot_grid(draw, _RW - int(260 * _SCALE), int(70 * _SCALE),
                   5, 4, int(44 * _SCALE), int(5 * _SCALE), _mix(p["bg"], p["accent"], 0.20))
    draw.rectangle([0, 0, _RW, int(8 * _SCALE)], fill=p["accent"])

    f_sm    = _font(21 * _SCALE)
    f_label = _font(24 * _SCALE, bold=True)
    max_w   = _RW - PAD * 2

    # Fit the giant stat
    stat_text, f_stat, h_stat = _fit_text_to_box(
        draw, stat, max_w, int(_RH * 0.34), [220, 190, 160, 130, 110, 90], gap=int(8 * _SCALE), max_lines=1
    )
    title_text, f_title, h_title = _fit_text_to_box(
        draw, title, max_w, int(_RH * 0.16), [52, 48, 44, 40, 36], gap=int(10 * _SCALE), max_lines=3
    )
    body_text, f_body, h_body = _fit_text_to_box(
        draw, body[:200], max_w - int(120 * _SCALE), int(_RH * 0.18), [32, 30, 28, 26, 24], gap=int(10 * _SCALE), max_lines=4
    )

    gap1, gap2 = int(30 * _SCALE), int(24 * _SCALE)
    block_h = h_stat + gap1 + h_title + gap2 + h_body
    y = (_RH - int(90 * _SCALE) - block_h) // 2

    # Giant stat, centered, accent
    for line in _wrap(draw, stat_text, f_stat, max_w):
        lw = _tw(draw, line, f_stat)
        draw.text(((_RW - lw) // 2, y), line, font=f_stat, fill=p["accent"])
        y += draw.textbbox((0, 0), line, font=f_stat)[3] + int(8 * _SCALE)
    y += gap1 - int(8 * _SCALE)

    # Title, centered
    for line in _wrap(draw, title_text, f_title, max_w):
        lw = _tw(draw, line, f_title)
        draw.text(((_RW - lw) // 2, y), line, font=f_title, fill=p["title"])
        y += draw.textbbox((0, 0), line, font=f_title)[3] + int(10 * _SCALE)
    y += gap2 - int(10 * _SCALE)

    # Caption, centered, subdued
    for line in _wrap(draw, body_text, f_body, max_w - int(120 * _SCALE)):
        lw = _tw(draw, line, f_body)
        draw.text(((_RW - lw) // 2, y), line, font=f_body, fill=p["subtitle"])
        y += draw.textbbox((0, 0), line, font=f_body)[3] + int(10 * _SCALE)

    # Footer
    footer_y = _RH - int(70 * _SCALE)
    draw.rectangle([PAD, footer_y, _RW - PAD, footer_y + int(1 * _SCALE)], fill=p["muted"])
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


def _wrap_height(draw, lines, font, gap):
    total = 0
    for i, line in enumerate(lines):
        total += draw.textbbox((0, 0), line, font=font)[3] + (gap if i < len(lines) - 1 else 0)
    return total


def _draw_centered_emphasis(draw, text, font, cx, y, max_w, base, accent, emphasis, gap):
    """Draw centered, wrapped text; words inside the emphasis phrase get the accent color."""
    emph = {w.strip('.,!?;:"\'').lower() for w in (emphasis or "").split()}
    space_w = _tw(draw, " ", font)
    for line in _wrap(draw, text, font, max_w):
        words = line.split()
        widths = [_tw(draw, w, font) for w in words]
        total = sum(widths) + space_w * (len(words) - 1)
        x = cx - total // 2
        lh = 0
        for w, ww in zip(words, widths):
            color = accent if w.strip('.,!?;:"\'').lower() in emph else base
            draw.text((x, y), w, font=font, fill=color)
            x += ww + space_w
            lh = draw.textbbox((0, 0), w, font=font)[3]
        y += lh + gap
    return y


def _slide_quote(headline: str, subtext: str, brand: str, p: dict,
                 emphasis: str = "", tag: str = "") -> Image.Image:
    """Standalone branded 'poster' image for a single text+image post.
    Centered composition with a top category tag and an accent-highlighted key phrase —
    deliberately distinct from the left-aligned carousel slides."""
    # Diagonal-feel gradient background
    img = Image.new("RGB", (_RW, _RH), p["bg"])
    _draw_gradient_bg(img, p["bg"], _mix(p["bg"], p["accent"], 0.24))
    draw = ImageDraw.Draw(img)

    # Big soft glow bottom-left + ghost ring top-right for depth
    _draw_circle(draw, -int(140 * _SCALE), _RH + int(120 * _SCALE),
                 int(560 * _SCALE), _mix(p["bg"], p["accent"], 0.12))
    _draw_circle(draw, _RW + int(120 * _SCALE), -int(120 * _SCALE),
                 int(440 * _SCALE), _mix(p["bg"], p["accent"], 0.10))
    _draw_dot_grid(draw, _RW - int(250 * _SCALE), _RH - int(170 * _SCALE),
                   5, 3, int(42 * _SCALE), int(5 * _SCALE), _mix(p["bg"], p["accent"], 0.18))

    cx = _RW // 2
    pad_x = int(110 * _SCALE)
    max_w = _RW - pad_x * 2

    # ── Top category tag (pill) ──
    tag = (tag or "").strip().upper()[:22]
    tag_bottom = int(150 * _SCALE)
    if tag:
        f_tag = _font(28 * _SCALE, bold=True)
        tw = _tw(draw, tag, f_tag)
        ph, pv = int(26 * _SCALE), int(13 * _SCALE)
        pill_w = tw + ph * 2
        th = draw.textbbox((0, 0), tag, font=f_tag)[3]
        pill_h = th + pv * 2
        px = cx - pill_w // 2
        py = int(120 * _SCALE)
        _draw_rounded_rect(draw, px, py, px + pill_w, py + pill_h, pill_h // 2,
                           _mix(p["bg"], p["accent"], 0.28))
        draw.text((px + ph, py + pv), tag, font=f_tag, fill=p["accent"])
        tag_bottom = py + pill_h

    # ── Center block: headline (with accent emphasis) + divider + subtext ──
    brand_reserved = int(150 * _SCALE)
    region_top = tag_bottom + int(40 * _SCALE)
    region_h = _RH - region_top - brand_reserved
    head_gap = int(14 * _SCALE)
    sub_gap = int(10 * _SCALE)
    divider_h = int(6 * _SCALE)
    gap_after_head = int(30 * _SCALE)
    gap_before_sub = int(22 * _SCALE)

    hook_text, f_head, _ = _fit_text_to_box(
        draw, headline, max_w, int(region_h * 0.66), [108, 98, 88, 80, 72, 64], gap=head_gap, max_lines=6
    )
    h_head = _wrap_height(draw, _wrap(draw, hook_text, f_head, max_w), f_head, head_gap)

    has_sub = bool((subtext or "").strip())
    if has_sub:
        sub_text, f_sub, _ = _fit_text_to_box(
            draw, subtext[:160], max_w, int(region_h * 0.22), [38, 36, 34, 32, 30], gap=sub_gap, max_lines=3
        )
        h_sub = _wrap_height(draw, _wrap(draw, sub_text, f_sub, max_w), f_sub, sub_gap)
    else:
        sub_text, f_sub, h_sub = "", _font(34 * _SCALE), 0

    block_h = h_head + (gap_after_head + divider_h + gap_before_sub + h_sub if has_sub else 0)
    y = region_top + max(0, (region_h - block_h) // 2)

    y = _draw_centered_emphasis(draw, hook_text, f_head, cx, y, max_w,
                                p["title"], p["accent"], emphasis, head_gap)
    if has_sub:
        y += gap_after_head - head_gap
        draw.rectangle([cx - int(40 * _SCALE), y, cx + int(40 * _SCALE), y + divider_h], fill=p["accent"])
        y += divider_h + gap_before_sub
        for line in _wrap(draw, sub_text, f_sub, max_w):
            lw = _tw(draw, line, f_sub)
            draw.text((cx - lw // 2, y), line, font=f_sub, fill=p["subtitle"])
            y += draw.textbbox((0, 0), line, font=f_sub)[3] + sub_gap

    # ── Brand bottom-center with accent dot ──
    f_brand = _font(26 * _SCALE, bold=True)
    bw = _tw(draw, brand, f_brand)
    dot_r = int(7 * _SCALE)
    gap = int(14 * _SCALE)
    total_w = dot_r * 2 + gap + bw
    bx = cx - total_w // 2
    by = _RH - int(96 * _SCALE)
    _draw_circle(draw, bx + dot_r, by + int(16 * _SCALE), dot_r, p["accent"])
    draw.text((bx + dot_r * 2 + gap, by), brand, font=f_brand, fill=p["subtitle"])

    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


# ── Single image post (branded quote card) ───────────────────────────────────

_IMAGE_POST_SYSTEM = """You create a single branded 'poster' image post for LinkedIn — a designed
graphic with one bold idea, NOT a slide from a deck.

Distill the content into ONE quotable, standalone idea, plus a caption.

card_tag: a 1-2 word category label shown at the top (e.g. "INSIGHT", "HOT TAKE", "THE SHIFT",
  "PREDICTION", "REALITY CHECK"). Pick what fits the idea.
card_headline: the single most striking, quotable line. Max 12 words. A bold claim, a sharp
  truth, or a reframe. Must make complete sense with zero context. No hashtags, no quote marks.
card_emphasis: the 1-4 word phrase WITHIN card_headline that carries the punch — it gets
  highlighted in color. MUST be an exact substring of card_headline (e.g. "predictive").
card_subtext: one supporting line that adds tension or a specific fact. Max 14 words.
  Optional — use "" if the headline stands strongest alone.
post_text: the LinkedIn caption. Hook first, no warm-up. 2-3 short paragraphs expanding the
  idea, varied rhythm. 0-3 lowercase hashtags on the last line.

VOICE: if a voice/author profile is provided, write the way that author talks.
FACTUAL SAFETY: never invent exact stats, product version numbers, dates, or named studies —
  hedge when unsure rather than fabricate a specific a reader could falsify.
NEVER USE: "It's not X. It's Y.", "Let that sink in", game-changer, unlock, leverage, synergy.

Return ONLY JSON: {"card_tag": "...", "card_headline": "...", "card_emphasis": "...",
"card_subtext": "...", "post_text": "..."}"""


def _finalize_image_post(result: dict) -> dict:
    from generator import _strip_markdown
    headline = _clean_slide_text(result.get("card_headline", ""))
    sub = (result.get("card_subtext") or "").strip()
    emphasis = _clean_slide_text(result.get("card_emphasis", "")) if result.get("card_emphasis") else ""
    # Only keep emphasis if it's genuinely part of the headline
    if emphasis and emphasis.lower() not in headline.lower():
        emphasis = ""
    return {
        "card_tag":      _clean_slide_text(result.get("card_tag", "")) if result.get("card_tag") else "",
        "card_headline": headline,
        "card_emphasis": emphasis,
        "card_subtext":  _clean_slide_text(sub) if sub else "",
        "post_text":     _strip_markdown(result.get("post_text", "") or ""),
    }


def generate_image_post_from_text(raw_text: str, company: dict = None) -> dict:
    voice_section = ""
    if company:
        from generator import _build_voice_block
        vb = _build_voice_block(company)
        if vb:
            voice_section = f"\n\nVOICE PROFILE:\n{vb[:1200]}"
    prompt = f"""Content to turn into a single branded image post:

{raw_text[:2000]}{voice_section}

Build the card around the single most quotable, scroll-stopping idea in the content.

Return ONLY valid JSON:
{{"card_tag": "...", "card_headline": "...", "card_emphasis": "...", "card_subtext": "...", "post_text": "..."}}"""
    result = generate_json(prompt, system=_IMAGE_POST_SYSTEM, max_tokens=1200, temperature=0.85)
    return _finalize_image_post(result)


def render_image_post_png(content: dict, company: dict) -> bytes:
    p = _get_palette(company)
    brand = company.get("name", "Voyce")
    img = _slide_quote(
        content.get("card_headline", ""), content.get("card_subtext", ""), brand, p,
        emphasis=content.get("card_emphasis", ""), tag=content.get("card_tag", ""),
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── Manual carousel (from pasted content) ────────────────────────────────────

_CAROUSEL_SYSTEM = """You are a world-class LinkedIn carousel strategist.

Your job: extract the most surprising, counterintuitive, or genuinely useful insight from the
content and build a carousel a busy professional would actually swipe through.

SAVE-WORTHY IS THE GOAL: carousels win on LinkedIn because people SAVE them, and a save drives
far more reach than a like. So make this a keepable reference — a clear framework, a numbered
playbook, a checklist, or a step-by-step — something a reader thinks "I'll need this later,"
not just "nice." Every slide must earn its place with a specific, usable takeaway.

HOOK SLIDE (most important):
- The headline is a specific, bold claim — a real number, a named company/event, a confession,
  or something that sounds wrong until explained.
- NEVER write: "X Is Important", "X Changes Everything", "The Future of X", "Why X Matters",
  "Top X Ways to...".
- The subtext adds a fact or tension — never restates the headline.

CONTENT SLIDES (3-5 slides — pick the count the content deserves, don't pad):
Each slide has a "kind":
- "point" (default): title = the point stated as a bold claim; body = a real company, report,
  number, or concrete example. label = a short tag for the slide (e.g. "STEP 1", "MYTH",
  "TRUTH", "MISTAKE 2") — pick labels that fit the carousel's narrative arc.
- "stat": use when one number IS the story. stat = the number itself (e.g. "43%", "₹2.4 Cr",
  "10x"); title = what the number means; body = one-sentence context with the source.
Use at most one stat slide. Vary the labels — a carousel where every label is "STEP n" is fine
for a how-to, wrong for a myth-buster.

CTA SLIDE:
- headline = the ONE thing to remember, stated as a declaration
- cta = a sharp question or next action for this specific audience — never "what do you think?"

VOICE: if a voice/author profile is provided, write every slide the way that author talks.

NEVER FABRICATE: do not invent first-person claims ("My conversion jumped", "I built", "we
grew") unless that exact claim appears in the provided content or author profile. Attribute
third-party results to their source ("Razorpay's report found...", "Founders interviewed said...").

OUTPUT HYGIENE (strict):
- No markdown (**, ##, backticks), no placeholder tokens like [X] or [Company], no numbered
  prefixes in titles.
- Banned: game-changer, landscape, unlock, dive deep, revolutionize, leverage, synergy,
  in today's world, the future is here, are you ready, I'm excited, at the end of the day,
  paradigm shift, move the needle, let that sink in.

post_text: the LinkedIn caption. Hook first, no warm-up. 3-4 short paragraphs, varied rhythm.
0-3 lowercase hashtags on the last line, only if relevant.

Return ONLY valid JSON."""

_CAROUSEL_SCHEMA = """{
  "hook_slide": {"headline": "...", "subtext": "..."},
  "content_slides": [
    {"kind": "point", "label": "STEP 1", "title": "...", "body": "..."},
    {"kind": "stat", "label": "THE NUMBER", "stat": "43%", "title": "...", "body": "..."},
    {"kind": "point", "label": "STEP 2", "title": "...", "body": "..."}
  ],
  "cta_slide": {"headline": "...", "cta": "..."},
  "post_text": "..."
}"""

def _clean_slide_text(text: str) -> str:
    """Strip markdown/template artifacts so rendered slides are publish-safe."""
    t = " ".join((text or "").split()).strip()
    if not t:
        return ""
    # Remove markdown emphasis/heading markers
    t = re.sub(r"[*_`#]+", "", t)
    # Remove leading numbering/bullets like "1. ", "2) ", "- "
    t = re.sub(r"^\s*(?:\d+[\.\)]\s+|[-•]\s+)", "", t)
    # Remove template placeholders like [Specific Number], [audience], [X]
    t = re.sub(r"\[[^\]]+\]", "", t)
    # Normalize spacing around punctuation after cleanup
    t = re.sub(r"\s+([.,!?;:])", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    # Fallback if model returns almost-empty tokenized text
    return t if len(t) >= 2 else "Insight"


def _sanitize_carousel_result(result: dict) -> dict:
    hook = result.get("hook_slide") or {}
    hook["headline"] = _clean_slide_text(hook.get("headline", ""))
    hook["subtext"] = _clean_slide_text(hook.get("subtext", ""))
    result["hook_slide"] = hook

    cleaned_content = []
    for s in (result.get("content_slides") or []):
        s = s or {}
        kind = s.get("kind", "point")
        cleaned = {
            "kind":  kind if kind in ("point", "stat") else "point",
            "label": _clean_slide_text(s.get("label", ""))[:24],
            "title": _clean_slide_text(s.get("title", "")),
            "body":  _clean_slide_text(s.get("body", "")),
        }
        if cleaned["kind"] == "stat":
            cleaned["stat"] = _clean_slide_text(s.get("stat", ""))[:14]
            if not cleaned["stat"]:
                cleaned["kind"] = "point"
        cleaned_content.append(cleaned)
    result["content_slides"] = cleaned_content[:5]

    cta = result.get("cta_slide") or {}
    cta["headline"] = _clean_slide_text(cta.get("headline", ""))
    cta["cta"] = _clean_slide_text(cta.get("cta", ""))
    result["cta_slide"] = cta

    if "post_text" in result:
        from generator import _strip_markdown
        result["post_text"] = _strip_markdown(result.get("post_text") or "")
    return result


def generate_carousel_from_text(raw_text: str, company: dict = None) -> dict:
    voice_section = ""
    if company:
        from generator import _build_voice_block
        voice_block = _build_voice_block(company)
        if voice_block:
            voice_section = f"\n\nVOICE PROFILE:\n{voice_block[:1200]}"

    prompt = f"""Content to turn into a LinkedIn carousel:

{raw_text[:2000]}{voice_section}

Build the carousel around the single most surprising or useful insight in the content.

Slide constraints:
- hook_slide.headline: max 8 words. hook_slide.subtext: max 12 words.
- content slide titles: max 6 words, stated as claims. Bodies: max 2 sentences with real specifics.
- 3-5 content slides — whatever the content deserves.
- cta_slide.headline: max 8 words. cta: max 15 words.

Return ONLY valid JSON in this shape:
{_CAROUSEL_SCHEMA}"""

    result = generate_json(prompt, system=_CAROUSEL_SYSTEM, max_tokens=1600, temperature=0.85)
    return _sanitize_carousel_result(result)


# ── Autonomous content generation ────────────────────────────────────────────

def generate_carousel_content(company: dict, news_context: str, post_type: str) -> dict:
    from autonomous import _build_company_brief, POST_TYPE_LABELS, _hook_guidance

    is_personal = company.get("profile_type") == "personal"
    voice_note = ("First person (I, my, I've) — write as if the author is speaking directly."
                  if is_personal else "Third person — company voice, authoritative.")
    company_brief = _build_company_brief(company)[:1400]

    prompt = f"""Generate a LinkedIn carousel for {company['name']} ({company['industry']}).

Post type: {POST_TYPE_LABELS.get(post_type, post_type)} — shape the narrative arc and slide labels to fit this type.
Latest industry context: {news_context or 'Draw from well-known industry examples and published reports.'}
Author/company context: {company_brief}

Slide constraints:
- hook_slide.headline: max 8 words, specific to this content. subtext: adds a fact or tension.
- 3-5 content slides. Titles max 6 words, stated as claims. Bodies cite real companies,
  reports, or numbers — nothing that could apply to any industry.
- cta_slide: the most memorable takeaway + a question this exact audience would care about.

{voice_note}
{_hook_guidance(company.get("allowed_hooks"))}

Return ONLY valid JSON in this shape:
{_CAROUSEL_SCHEMA}"""

    result = generate_json(prompt, system=_CAROUSEL_SYSTEM, max_tokens=1600, temperature=0.85)
    return _sanitize_carousel_result(result)


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
        num = 2 + i
        if s.get("kind") == "stat" and s.get("stat"):
            slides.append(_slide_stat(s["stat"], s.get("title", ""), s.get("body", ""),
                                      num, total, brand, p))
        else:
            slides.append(_slide_content(s.get("title", ""), s.get("body", ""), num, total,
                                         brand, p, label=s.get("label", "")))
    slides.append(_slide_cta(cta["headline"], cta["cta"], total, total, brand, p))

    buf = io.BytesIO()
    slides[0].save(buf, format="PDF", save_all=True, append_images=slides[1:], resolution=300)
    return buf.getvalue()
