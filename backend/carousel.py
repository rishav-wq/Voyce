import colorsys
import io
import os
import re

from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

from llm import generate_json

load_dotenv()

SLIDE_W, SLIDE_H = 1080, 1350       # portrait 4:5 — max uncropped vertical space on mobile (research-backed)
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
    "warm_violet": {                    # Voyce brand — warm beige + violet (default)
        "bg":       (244, 240, 232),   # #f4f0e8 warm beige canvas
        "accent":   (108, 71,  255),   # #6c47ff violet
        "title":    (28,  24,  19),    # #1c1813 warm black
        "subtitle": (92,  86,  78),    # muted warm grey
        "body":     (60,  56,  50),    # dark warm grey
        "muted":    (196, 188, 174),   # light warm grey
    },
}

# ── Industry → palette mapping ────────────────────────────────────────────────
# Secondary "electric" accent per palette — the two-tone pop (purple+lime energy).
# Used for highlight pills, alternating labels, and outline numerals.
_ACCENT2 = {
    "dark_pro":    (163, 230, 53),    # navy/cyan  + lime
    "warm_dark":   (56, 189, 248),    # charcoal/amber + sky
    "deep_teal":   (251, 191, 36),    # teal/emerald + amber
    "deep_indigo": (163, 230, 53),    # indigo/violet + lime
    "clean_light": (249, 115, 22),    # white/indigo + orange
    "warm_paper":  (13, 148, 136),    # cream/orange + deep teal
    "electric":    (167, 139, 250),   # black/lime + violet
    "warm_violet": (163, 230, 53),    # beige/violet + lime
}


for _k, _v in _ACCENT2.items():
    if _k in PALETTES:
        PALETTES[_k]["accent2"] = _v


def _a2(p: dict) -> tuple:
    return p.get("accent2", (163, 230, 53))


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
    "agri":          "deep_teal",
    "agriculture":   "deep_teal",
    "farming":       "deep_teal",
    "food":          "deep_teal",
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

    # 3. Industry keyword match — on word boundaries, so short keys like "ai"
    # can't match inside words ("chain", "training").
    industry = company.get("industry", "").lower()
    words = re.findall(r"[a-z]+", industry)
    for key, palette_name in _INDUSTRY_MAP.items():
        if " " in key:
            if key in industry:
                return PALETTES[palette_name]
        elif any(w == key or (len(key) >= 4 and w.startswith(key)) for w in words):
            # prefix match only for longer keys ("agri" -> agritech), never "ai" -> "aid"
            return PALETTES[palette_name]

    # 4. Default → Voyce brand: warm beige + violet (on-brand, high contrast, light)
    return PALETTES["warm_violet"]


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

def _font_black(size: int) -> ImageFont.FreeTypeFont:
    """Inter Black (900) — the display weight for covers; falls back to Bold."""
    try:
        return ImageFont.truetype(os.path.join(_FONTS_DIR, "Inter-Black.ttf"), int(size))
    except Exception:
        return _font(size, bold=True)


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

def _split_hook_pill(headline: str) -> tuple:
    """Pull a short kicker phrase out of the headline to render as a highlight pill.
    'Stop building apps for farmers. Do this instead.' -> ('Stop building apps for farmers.',
    'Do this instead'). Falls back to the last two words when they're short enough."""
    hl = (headline or "").strip()
    parts = re.split(r"(?<=[.!?])\s+", hl)
    if len(parts) >= 2 and 3 <= len(parts[-1].rstrip(".!?")) <= 30:
        return " ".join(parts[:-1]).strip(), parts[-1].rstrip(".!?").strip()
    words = hl.split()
    if len(words) > 5:
        cand = " ".join(words[-2:]).rstrip(".!?")
        if len(cand) <= 22:
            return " ".join(words[:-2]).strip(), cand
    return hl, ""


def _slide_hook_number_block(n_text: str, rest: str, kicker: str, subtext: str,
                             total: int, p: dict, brand: str = "") -> Image.Image:
    """Number-block cover: ink band with the giant count ('3 WAYS') stacked on an
    accent2 block with mixed-scale headline + outlined kicker pill + arrow. The
    highest-energy cover in the kit — auto-used when the hook leads with a number."""
    ink = (18, 16, 24)
    img = Image.new("RGB", (_RW, _RH), _mix(p["bg"], ink, 0.5))
    _draw_gradient_bg(img, _mix(p["bg"], ink, 0.35), _mix(p["bg"], ink, 0.65))
    draw = ImageDraw.Draw(img)

    a2 = _a2(p)
    m = int(90 * _SCALE)
    block_l, block_r = m, _RW - m

    # Measure pieces to center the stack
    f_band = _font(120 * _SCALE, bold=True)
    band_txt = n_text.upper()[:12]
    band_h = draw.textbbox((0, 0), band_txt, font=f_band)[3] + int(56 * _SCALE)

    pad_in = int(44 * _SCALE)
    inner_w = block_r - block_l - pad_in * 2
    f_small = _font(34 * _SCALE, semi=True)
    rest_fit, f_rest, h_rest = _fit_text_to_box(
        draw, rest.upper(), inner_w, int(_RH * 0.28), [88, 78, 70, 62, 54], gap=int(10 * _SCALE), max_lines=3)
    small_h = draw.textbbox((0, 0), "X", font=f_small)[3]

    f_kick = _font(26 * _SCALE, bold=True)
    kick_h = (draw.textbbox((0, 0), kicker, font=f_kick)[3] + int(28 * _SCALE)) if kicker else 0

    yellow_h = pad_in + small_h + int(20 * _SCALE) + h_rest + (int(34 * _SCALE) + kick_h if kicker else 0) + pad_in
    total_h = band_h + yellow_h
    y0 = max(int(130 * _SCALE), (_RH - total_h - int(120 * _SCALE)) // 2)

    # Ink band with the count
    draw.rectangle([block_l, y0, block_r, y0 + band_h], fill=ink)
    bw = _tw(draw, band_txt, f_band)
    draw.text((block_l + (block_r - block_l - bw) // 2, y0 + int(20 * _SCALE)),
              band_txt, font=f_band, fill=(255, 255, 255))

    # Accent2 block
    by0 = y0 + band_h
    draw.rectangle([block_l, by0, block_r, by0 + yellow_h], fill=a2)
    ty = by0 + pad_in
    sub_line = (subtext or "").upper()[:44]
    if sub_line:
        sw = _tw(draw, sub_line, f_small)
        draw.text((block_l + (block_r - block_l - sw) // 2, ty), sub_line, font=f_small, fill=ink)
    ty += small_h + int(20 * _SCALE)
    for line in _wrap(draw, rest_fit, f_rest, inner_w):
        lw = _tw(draw, line, f_rest)
        draw.text((block_l + (block_r - block_l - lw) // 2, ty), line, font=f_rest, fill=ink)
        ty += draw.textbbox((0, 0), line, font=f_rest)[3] + int(10 * _SCALE)
    if kicker:
        ty += int(24 * _SCALE)
        kw = _tw(draw, kicker, f_kick)
        kick_pad = int(26 * _SCALE)
        kx = block_l + (block_r - block_l - kw - kick_pad * 2) // 2
        kh = draw.textbbox((0, 0), kicker, font=f_kick)[3] + int(24 * _SCALE)
        _draw_rounded_rect(draw, kx, ty, kx + kw + kick_pad * 2, ty + kh, kh // 2, a2)
        draw.rounded_rectangle([kx, ty, kx + kw + kick_pad * 2, ty + kh], radius=kh // 2,
                               outline=ink, width=max(3, int(4 * _SCALE)))
        draw.text((kx + kick_pad, ty + int(11 * _SCALE)), kicker, font=f_kick, fill=ink)

    # Brand chip + swipe arrow + counter
    f_chip = _font(24 * _SCALE, bold=True)
    _draw_circle(draw, m + int(8 * _SCALE), int(72 * _SCALE), int(7 * _SCALE), a2)
    if brand:
        draw.text((m + int(26 * _SCALE), int(58 * _SCALE)), brand[:26], font=f_chip, fill=(210, 205, 220))
    ar = int(34 * _SCALE)
    _draw_circle(draw, _RW - m - ar, _RH - int(130 * _SCALE), ar, a2)
    f_ar = _font(40 * _SCALE, bold=True)
    aw = _tw(draw, "→", f_ar)
    draw.text((_RW - m - ar - aw // 2, _RH - int(130 * _SCALE) - int(26 * _SCALE)), "→", font=f_ar, fill=ink)
    f_sm = _font(21 * _SCALE)
    draw.text((m, _RH - int(120 * _SCALE)), f"1 / {total}", font=f_sm, fill=(160, 155, 170))

    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


def _slide_hook_editorial(headline: str, subtext: str, total: int, p: dict,
                          brand: str = "", n_steps: int = 0) -> Image.Image:
    """The approved v3 cover, ported: ink canvas, white bold headline with the kicker
    phrase in an accent2 marker block, gray sub, outlined promise chip, accent2 arrow
    button, progress dashes. Restrained and editorial — lime as a scalpel, not a slab."""
    ink = (23, 21, 31)
    a2 = _a2(p)
    img = Image.new("RGB", (_RW, _RH), ink)
    _draw_gradient_bg(img, ink, _mix(ink, p["accent"], 0.10))
    draw = ImageDraw.Draw(img)

    m = int(84 * _SCALE)
    max_w = _RW - m * 2

    # Brand chip
    f_chip = _font(26 * _SCALE, bold=True)
    _draw_circle(draw, m + int(8 * _SCALE), int(86 * _SCALE), int(8 * _SCALE), a2)
    if brand:
        draw.text((m + int(28 * _SCALE), int(70 * _SCALE)), brand[:26], font=f_chip, fill=(164, 157, 179))

    # Kicker chip: when the hook leads with a count, the chip IS the count ("3 RULES") in
    # accent2 — the slab's glanceable what-you're-getting clarity, editorial delivery.
    n_txt, n_rest = _split_number_hook(headline)
    if n_txt:
        kick_txt, kick_bg, kick_fg = n_txt.upper(), a2, ink
        f_kick = _font(34 * _SCALE, bold=True)
        headline_for_body = n_rest
    else:
        kick_txt, kick_bg, kick_fg = ("THE PLAYBOOK" if n_steps >= 3 else "READ THIS"), p["accent"], (255, 255, 255)
        f_kick = _font(24 * _SCALE, bold=True)
        headline_for_body = headline
    kw = _tw(draw, kick_txt, f_kick)
    kx, ky = m, int(180 * _SCALE)
    kh = draw.textbbox((0, 0), kick_txt, font=f_kick)[3] + int(20 * _SCALE)
    _draw_rounded_rect(draw, kx, ky, kx + kw + int(36 * _SCALE), ky + kh, int(10 * _SCALE), kick_bg)
    draw.text((kx + int(18 * _SCALE), ky + int(9 * _SCALE)), kick_txt, font=f_kick, fill=kick_fg)

    # Headline: Inter-Black display weight, main lines white, kicker phrase as a
    # slightly TILTED accent2 marker block with ink text (the v3 signature).
    head_main, marked = _split_hook_pill(headline_for_body)
    head_gap = int(14 * _SCALE)
    y = ky + kh + int(48 * _SCALE)
    sizes_black = [120, 110, 100, 92, 84]
    f_head = None
    for s in sizes_black:
        f_try = _font_black(s * _SCALE)
        if _wrap_height(draw, _wrap(draw, head_main, f_try, max_w), f_try, head_gap) <= int(_RH * 0.36) \
           and len(_wrap(draw, head_main, f_try, max_w)) <= 3:
            f_head = f_try
            break
    f_head = f_head or _font_black(sizes_black[-1] * _SCALE)
    for line in _wrap(draw, head_main, f_head, max_w):
        draw.text((m, y), line, font=f_head, fill=(255, 253, 248))
        y += draw.textbbox((0, 0), line, font=f_head)[3] + head_gap
    if marked:
        y += int(8 * _SCALE)
        mk_pad_x, mk_pad_y = int(24 * _SCALE), int(12 * _SCALE)
        mk_txt = marked + "."
        mw = _tw(draw, mk_txt, f_head)
        mh = draw.textbbox((0, 0), mk_txt, font=f_head)[3] + mk_pad_y * 2
        pad_rot = int(30 * _SCALE)
        chip = Image.new("RGBA", (mw + mk_pad_x * 2 + pad_rot * 2, mh + pad_rot * 2), (0, 0, 0, 0))
        cd = ImageDraw.Draw(chip)
        _draw_rounded_rect(cd, pad_rot, pad_rot, pad_rot + mw + mk_pad_x * 2, pad_rot + mh,
                           int(14 * _SCALE), a2)
        cd.text((pad_rot + mk_pad_x, pad_rot + mk_pad_y - int(2 * _SCALE)), mk_txt, font=f_head, fill=ink)
        chip = chip.rotate(-1.5, expand=True, resample=Image.BICUBIC)
        img.paste(chip, (m - pad_rot, y - pad_rot), chip)
        y += mh + int(10 * _SCALE)
    y += int(40 * _SCALE)

    # Sub
    f_sub = _font(34 * _SCALE)
    sub_fit, f_sub, _ = _fit_text_to_box(
        draw, (subtext or "")[:160], int(max_w * 0.9), int(_RH * 0.14), [36, 33, 30], gap=int(9 * _SCALE), max_lines=3)
    for line in _wrap(draw, sub_fit, f_sub, int(max_w * 0.9)):
        draw.text((m, y), line, font=f_sub, fill=(164, 157, 179))
        y += draw.textbbox((0, 0), line, font=f_sub)[3] + int(9 * _SCALE)

    # Promise chip (outlined)
    y += int(34 * _SCALE)
    f_pr = _font(25 * _SCALE, bold=True)
    n_part = f"{n_steps} rules · " if n_steps >= 3 else ""
    pr_txt = f"{n_part}2 min read · save it"
    pw = _tw(draw, pr_txt, f_pr)
    ph = draw.textbbox((0, 0), pr_txt, font=f_pr)[3] + int(24 * _SCALE)
    draw.rounded_rectangle([m, y, m + pw + int(44 * _SCALE), y + ph], radius=ph // 2,
                           outline=(74, 69, 88), width=max(3, int(3 * _SCALE)))
    draw.text((m + int(22 * _SCALE), y + int(11 * _SCALE)), pr_txt, font=f_pr, fill=(207, 200, 221))

    # Arrow button + progress dashes + counter
    ar = int(40 * _SCALE)
    _draw_circle(draw, _RW - m - ar, _RH - int(150 * _SCALE), ar, a2)
    f_ar = _font(44 * _SCALE, bold=True)
    aw = _tw(draw, "→", f_ar)
    draw.text((_RW - m - ar - aw // 2, _RH - int(150 * _SCALE) - int(30 * _SCALE)), "→", font=f_ar, fill=ink)
    dy = _RH - int(96 * _SCALE)
    for i in range(4):
        col = a2 if i == 0 else (74, 69, 88)
        draw.rounded_rectangle([m + i * int(46 * _SCALE), dy, m + i * int(46 * _SCALE) + int(34 * _SCALE),
                                dy + int(7 * _SCALE)], radius=int(3 * _SCALE), fill=col)
    f_sm = _font(22 * _SCALE)
    cw = _tw(draw, f"1 / {total}", f_sm)
    draw.text((_RW - m - cw, dy - int(4 * _SCALE)), f"1 / {total}", font=f_sm, fill=(140, 134, 155))

    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


_INK_V3 = (23, 21, 31)
_CREAM_V3 = (244, 240, 232)


def _chip_v3(draw, brand: str, p: dict, on_dark: bool, m: int):
    if not brand:
        return
    f_chip = _font(24 * _SCALE, bold=True)
    fg = (164, 157, 179) if on_dark else (122, 114, 99)
    _draw_circle(draw, m + int(8 * _SCALE), int(82 * _SCALE), int(7 * _SCALE), _a2(p))
    draw.text((m + int(26 * _SCALE), int(68 * _SCALE)), brand[:26], font=f_chip, fill=fg)


def _footer_v3(draw, num: int, total: int, p: dict, on_dark: bool, m: int, teaser: str = ""):
    a2 = _a2(p)
    dim = (74, 69, 88) if on_dark else (214, 207, 194)
    txt = (207, 200, 221) if on_dark else (122, 114, 99)
    if teaser:
        f_t = _font(24 * _SCALE, bold=True)
        ty = _RH - int(158 * _SCALE)
        draw.text((m, ty), "→", font=f_t, fill=a2)
        draw.text((m + int(34 * _SCALE), ty), f"next: {teaser[:52]}", font=f_t, fill=txt)
    dy = _RH - int(96 * _SCALE)
    on_idx = 0 if total <= 1 else round((num - 1) / (total - 1) * 3)
    for i in range(4):
        col = a2 if i == on_idx else dim
        draw.rounded_rectangle([m + i * int(46 * _SCALE), dy,
                                m + i * int(46 * _SCALE) + int(34 * _SCALE), dy + int(7 * _SCALE)],
                               radius=int(3 * _SCALE), fill=col)
    f_sm = _font(22 * _SCALE)
    ctr = f"{num} / {total}"
    draw.text((_RW - m - _tw(draw, ctr, f_sm), dy - int(4 * _SCALE)), ctr, font=f_sm, fill=txt)


def _slide_body_v3(title: str, body: str, num: int, total: int, brand: str, p: dict,
                   label: str = "", step: int = None, teaser: str = "") -> Image.Image:
    """Cream editorial body slide: outlined giant numeral, ink label pill, black-weight
    headline, body in a white card with an ink border and hard accent offset-shadow."""
    img = Image.new("RGB", (_RW, _RH), _CREAM_V3)
    draw = ImageDraw.Draw(img)
    m = int(84 * _SCALE)
    max_w = _RW - m * 2

    if step:
        f_giant = _font_black(300 * _SCALE)
        pn = str(step).zfill(2)
        draw.text((_RW - m - _tw(draw, pn, f_giant) + int(40 * _SCALE), -int(50 * _SCALE)), pn,
                  font=f_giant, fill=_CREAM_V3, stroke_width=max(3, int(3 * _SCALE)),
                  stroke_fill=(217, 210, 196))
    _chip_v3(draw, brand, p, on_dark=False, m=m)

    y = int(_RH * 0.20)
    lab = (label or (f"RULE {step}" if step else "START HERE")).upper()[:22]
    f_lab = _font(24 * _SCALE, bold=True)
    lw = _tw(draw, lab, f_lab)
    lh = draw.textbbox((0, 0), lab, font=f_lab)[3] + int(18 * _SCALE)
    _draw_rounded_rect(draw, m, y, m + lw + int(32 * _SCALE), y + lh, lh // 2, _INK_V3)
    draw.text((m + int(16 * _SCALE), y + int(8 * _SCALE)), lab, font=f_lab, fill=_a2(p))
    y += lh + int(34 * _SCALE)

    head_gap = int(12 * _SCALE)
    t_fit, f_title, h_t = _fit_text_to_box(
        draw, title, max_w, int(_RH * 0.20), [72, 66, 60, 54, 48], gap=head_gap, max_lines=2)
    f_title = _font_black(f_title.size)
    for line in _wrap(draw, t_fit, f_title, max_w):
        draw.text((m, y), line, font=f_title, fill=_INK_V3)
        y += draw.textbbox((0, 0), line, font=f_title)[3] + head_gap
    y += int(40 * _SCALE)

    # White card + accent offset shadow
    pad = int(34 * _SCALE)
    body_gap = int(13 * _SCALE)
    text_w = max_w - pad * 2
    avail = _RH - int(190 * _SCALE) - y - pad * 2
    b_fit, f_body, _ = _fit_text_to_box(
        draw, body[:260], text_w, max(int(120 * _SCALE), avail), [40, 37, 34, 31, 28], gap=body_gap, max_lines=6)
    lines = _wrap(draw, b_fit, f_body, text_w)
    card_h = _wrap_height(draw, lines, f_body, body_gap) + pad * 2
    off = int(9 * _SCALE)
    r = int(20 * _SCALE)
    _draw_rounded_rect(draw, m + off, y + off, m + max_w + off, y + card_h + off, r, p["accent"])
    _draw_rounded_rect(draw, m, y, m + max_w, y + card_h, r, (255, 253, 248))
    draw.rounded_rectangle([m, y, m + max_w, y + card_h], radius=r, outline=_INK_V3,
                           width=max(3, int(3 * _SCALE)))
    ty = y + pad
    for line in lines:
        draw.text((m + pad, ty), line, font=f_body, fill=(58, 53, 70))
        ty += draw.textbbox((0, 0), line, font=f_body)[3] + body_gap

    _footer_v3(draw, num, total, p, on_dark=False, m=m, teaser=teaser)
    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


def _slide_stat_v3(stat: str, title: str, body: str, num: int, total: int,
                   brand: str, p: dict, teaser: str = "") -> Image.Image:
    """Accent color-block stat slide: giant accent2 numeral, bold what-line, soft why-line."""
    img = Image.new("RGB", (_RW, _RH), p["accent"])
    draw = ImageDraw.Draw(img)
    m = int(84 * _SCALE)
    max_w = _RW - m * 2
    # Ghost ring bottom-right
    for rr in (int(300 * _SCALE),):
        draw.ellipse([_RW - rr, _RH - rr, _RW + rr // 2, _RH + rr // 2],
                     outline=_mix(p["accent"], (255, 255, 255), 0.14), width=int(40 * _SCALE))
    _chip_v3(draw, brand, p, on_dark=True, m=m)

    f_stat = None
    for s in [170, 150, 130, 110, 90]:
        f_try = _font_black(s * _SCALE)
        if len(_wrap(draw, stat, f_try, max_w)) <= 2:
            f_stat = f_try
            break
    f_stat = f_stat or _font_black(90 * _SCALE)
    st_lines = _wrap(draw, stat, f_stat, max_w)
    h_stat = _wrap_height(draw, st_lines, f_stat, int(6 * _SCALE))

    f_what = _font(42 * _SCALE, bold=True)
    what_fit, f_what, h_what = _fit_text_to_box(
        draw, title, max_w, int(_RH * 0.16), [44, 40, 36, 32], gap=int(10 * _SCALE), max_lines=3)
    f_why = _font(30 * _SCALE)
    why_fit, f_why, h_why = _fit_text_to_box(
        draw, body[:180], int(max_w * 0.92), int(_RH * 0.14), [31, 29, 27], gap=int(9 * _SCALE), max_lines=3)

    block = h_stat + int(26 * _SCALE) + h_what + int(22 * _SCALE) + h_why
    y = max(int(_RH * 0.18), (_RH - int(190 * _SCALE) - block) // 2)
    for line in st_lines:
        draw.text((m, y), line, font=f_stat, fill=_a2(p))
        y += draw.textbbox((0, 0), line, font=f_stat)[3] + int(6 * _SCALE)
    y += int(26 * _SCALE)
    for line in _wrap(draw, what_fit, f_what, max_w):
        draw.text((m, y), line, font=f_what, fill=(255, 255, 255))
        y += draw.textbbox((0, 0), line, font=f_what)[3] + int(10 * _SCALE)
    y += int(22 * _SCALE)
    soft = _mix(p["accent"], (255, 255, 255), 0.62)
    for line in _wrap(draw, why_fit, f_why, int(max_w * 0.92)):
        draw.text((m, y), line, font=f_why, fill=soft)
        y += draw.textbbox((0, 0), line, font=f_why)[3] + int(9 * _SCALE)

    _footer_v3(draw, num, total, p, on_dark=True, m=m, teaser=teaser)
    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


def _slide_recap_v3(title: str, bullets: list, num: int, total: int,
                    brand: str, p: dict) -> Image.Image:
    """Cream recap: black-weight title + accent2-check list."""
    img = Image.new("RGB", (_RW, _RH), _CREAM_V3)
    draw = ImageDraw.Draw(img)
    m = int(84 * _SCALE)
    max_w = _RW - m * 2
    _chip_v3(draw, brand, p, on_dark=False, m=m)

    y = int(_RH * 0.16)
    t_fit, f_t, _ = _fit_text_to_box(draw, title, max_w, int(_RH * 0.16),
                                     [64, 58, 52, 46], gap=int(12 * _SCALE), max_lines=2)
    f_t = _font_black(f_t.size)
    for line in _wrap(draw, t_fit, f_t, max_w):
        draw.text((m, y), line, font=f_t, fill=_INK_V3)
        y += draw.textbbox((0, 0), line, font=f_t)[3] + int(12 * _SCALE)
    y += int(44 * _SCALE)

    items = [str(b).strip() for b in (bullets or []) if str(b).strip()][:5]
    f_b = _font(36 * _SCALE, semi=True)
    ck_r = int(22 * _SCALE)
    tx = m + ck_r * 2 + int(26 * _SCALE)
    text_w = _RW - m - tx
    for it in items:
        lines = _wrap(draw, it[:110], f_b, text_w)
        ih = _wrap_height(draw, lines, f_b, int(8 * _SCALE))
        if y + ih > _RH - int(190 * _SCALE):
            break
        _draw_circle(draw, m + ck_r, y + int(24 * _SCALE), ck_r, _a2(p))
        ckw = max(3, int(5 * _SCALE))
        cx, cy = m + ck_r, y + int(24 * _SCALE)
        draw.line([(cx - int(10 * _SCALE), cy), (cx - int(3 * _SCALE), cy + int(8 * _SCALE)),
                   (cx + int(11 * _SCALE), cy - int(8 * _SCALE))], fill=_INK_V3, width=ckw)
        ty = y
        for line in lines:
            draw.text((tx, ty), line, font=f_b, fill=(58, 53, 70))
            ty += draw.textbbox((0, 0), line, font=f_b)[3] + int(8 * _SCALE)
        y += ih + int(30 * _SCALE)

    _footer_v3(draw, num, total, p, on_dark=False, m=m)
    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


def _slide_cta_v3(headline: str, cta: str, total: int, brand: str, p: dict) -> Image.Image:
    """Ink CTA: centered black-weight headline, accent2 pill button, save line."""
    img = Image.new("RGB", (_RW, _RH), _INK_V3)
    _draw_gradient_bg(img, _INK_V3, _mix(_INK_V3, p["accent"], 0.14))
    draw = ImageDraw.Draw(img)
    m = int(84 * _SCALE)
    max_w = _RW - m * 2
    _draw_circle(draw, -int(60 * _SCALE), -int(60 * _SCALE), int(220 * _SCALE),
                 _mix(_INK_V3, p["accent"], 0.55))
    _chip_v3(draw, brand, p, on_dark=True, m=m)

    h_fit, f_h, h_h = _fit_text_to_box(draw, headline, max_w, int(_RH * 0.26),
                                       [84, 76, 68, 60, 52], gap=int(12 * _SCALE), max_lines=3)
    f_h = _font_black(f_h.size)
    f_cta = _font(34 * _SCALE, bold=True)
    cta_txt = (cta or "").strip()[:48]
    ch = draw.textbbox((0, 0), cta_txt, font=f_cta)[3] + int(38 * _SCALE)
    save_txt = "Save this for your next content sprint"
    f_sv = _font(26 * _SCALE)
    block = h_h + int(52 * _SCALE) + ch + int(28 * _SCALE) + draw.textbbox((0, 0), save_txt, font=f_sv)[3]
    y = max(int(_RH * 0.2), (_RH - int(160 * _SCALE) - block) // 2)

    for line in _wrap(draw, h_fit, f_h, max_w):
        lw = _tw(draw, line, f_h)
        draw.text(((_RW - lw) // 2, y), line, font=f_h, fill=(255, 253, 248))
        y += draw.textbbox((0, 0), line, font=f_h)[3] + int(12 * _SCALE)
    y += int(40 * _SCALE)
    if cta_txt:
        cw = _tw(draw, cta_txt, f_cta)
        cx0 = (_RW - cw - int(80 * _SCALE)) // 2
        _draw_rounded_rect(draw, cx0, y, cx0 + cw + int(80 * _SCALE), y + ch, ch // 2, _a2(p))
        draw.text((cx0 + int(40 * _SCALE), y + int(17 * _SCALE)), cta_txt, font=f_cta, fill=_INK_V3)
        y += ch + int(28 * _SCALE)
    sw = _tw(draw, save_txt, f_sv)
    draw.text(((_RW - sw) // 2, y), save_txt, font=f_sv, fill=(164, 157, 179))

    _footer_v3(draw, total, total, p, on_dark=True, m=m)
    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


def _split_number_hook(headline: str) -> tuple:
    """'3 ways to make your brand stand out' -> ('3 WAYS', 'to make your brand stand out').
    Returns ('', '') when the hook doesn't lead with a count."""
    mm = re.match(r"^\s*(\d{1,2})\s+(\w+)\s+(.{8,})$", (headline or "").strip())
    if mm and int(mm.group(1)) <= 20:
        return f"{mm.group(1)} {mm.group(2)}", mm.group(3).strip(" .")
    return "", ""


def _slide_hook(headline: str, subtext: str, num: int, total: int,
                p: dict, brand: str = "") -> Image.Image:
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

    head_main, pill_phrase = _split_hook_pill(headline)
    hook_text, f_head, h_head = _fit_text_to_box(
        draw, head_main, max_w, int(safe_h * 0.58), [112, 104, 96, 88, 80, 72], gap=head_gap, max_lines=5
    )
    sub_text, f_sub, h_sub = _fit_text_to_box(
        draw, subtext[:180], max_w, int(safe_h * 0.26), [38, 36, 34, 32, 30, 28], gap=sub_gap, max_lines=4
    )
    # Highlight pill sizing (the two-tone kicker chip)
    pill_block_h = 0
    f_pill = _font(44 * _SCALE, bold=True)
    pill_pad_x, pill_pad_y = int(30 * _SCALE), int(14 * _SCALE)
    gap_before_pill = int(26 * _SCALE)
    if pill_phrase:
        pill_txt_h = draw.textbbox((0, 0), pill_phrase, font=f_pill)[3]
        pill_block_h = gap_before_pill + pill_txt_h + pill_pad_y * 2

    block_h = h_head + pill_block_h + gap_after_head + divider_h + gap_before_sub + h_sub
    y = max(margin, (_RH - footer_reserved - block_h) // 2)

    # Brand chip top-left (small dot + name)
    if brand:
        f_chip = _font(24 * _SCALE, bold=True)
        chip_y = margin + int(10 * _SCALE)
        _draw_circle(draw, text_x + int(7 * _SCALE), chip_y + int(14 * _SCALE), int(7 * _SCALE), _a2(p))
        draw.text((text_x + int(26 * _SCALE), chip_y), brand[:26], font=f_chip, fill=p["subtitle"])

    # Headline
    y += _put_text(draw, hook_text, f_head, text_x, y, max_w, p["title"], gap=head_gap)

    # Highlight pill: the kicker phrase in the electric secondary color
    if pill_phrase:
        y += gap_before_pill
        tw_pill = _tw(draw, pill_phrase, f_pill)
        pill_txt_h = draw.textbbox((0, 0), pill_phrase, font=f_pill)[3]
        ph = pill_txt_h + pill_pad_y * 2
        _draw_rounded_rect(draw, text_x, y, text_x + tw_pill + pill_pad_x * 2, y + ph,
                           ph // 2, _a2(p))
        draw.text((text_x + pill_pad_x, y + pill_pad_y - int(2 * _SCALE)), pill_phrase,
                  font=f_pill, fill=(15, 15, 18))
        y += ph
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
                   brand: str, p: dict, label: str = "", step_index: int = None,
                   teaser: str = "") -> Image.Image:
    """Content slide: split layout — giant clipped number left, accent panel right.
    step_index is the 1-based content-step number for the giant numeral/label (defaults to num-1)."""
    step = step_index if step_index is not None else num - 1
    body = body[:200].rsplit(" ", 1)[0] if len(body) > 200 else body

    # Slightly lighter background
    bg2 = _mix(p["bg"], (255, 255, 255), 0.04)
    img = Image.new("RGB", (_RW, _RH), bg2)
    draw = ImageDraw.Draw(img)

    # ── Top accent bar (full width) ──
    draw.rectangle([0, 0, _RW, int(8 * _SCALE)], fill=p["accent"])

    # ── Subtle dot-grid accent top-right (keeps the frame alive, no empty panel) ──
    _draw_dot_grid(draw, _RW - int(250 * _SCALE), int(80 * _SCALE),
                   5, 3, int(44 * _SCALE), int(5 * _SCALE), _mix(bg2, p["accent"], 0.18))

    content_x = PAD
    max_w = _RW - PAD * 2

    # ── Giant step number, top-left. Alternate treatment per step for deck variety:
    # odd steps = solid faded accent fill, even steps = electric accent2 OUTLINE numeral.
    f_giant = _font(300 * _SCALE, bold=True)
    point_num = str(step).zfill(2)
    if step % 2 == 0:
        draw.text((PAD - int(6 * _SCALE), int(_RH * 0.085)), point_num, font=f_giant,
                  fill=bg2, stroke_width=max(2, int(3 * _SCALE)),
                  stroke_fill=_mix(bg2, _a2(p), 0.75))
    else:
        draw.text((PAD - int(6 * _SCALE), int(_RH * 0.085)), point_num,
                  font=f_giant, fill=_mix(bg2, p["accent"], 0.20))

    f_label = _font(24 * _SCALE, bold=True)
    f_sm    = _font(22 * _SCALE)
    footer_y = _RH - int(70 * _SCALE)

    # ── Content: label → underline → title → body, full width, lower half ──
    content_y = int(_RH * 0.40)

    accent_this = _a2(p) if step % 2 == 0 else p["accent"]
    label_text = (label or f"STEP {step}").upper()[:24]
    # Label as a small filled pill on even steps (the two-tone treatment)
    if step % 2 == 0:
        lw = _tw(draw, label_text, f_label)
        lph, lpv = int(18 * _SCALE), int(8 * _SCALE)
        lth = draw.textbbox((0, 0), label_text, font=f_label)[3]
        _draw_rounded_rect(draw, content_x, content_y - lpv, content_x + lw + lph * 2,
                           content_y + lth + lpv, (lth + lpv * 2) // 2, accent_this)
        draw.text((content_x + lph, content_y), label_text, font=f_label, fill=(15, 15, 18))
    else:
        draw.text((content_x, content_y), label_text, font=f_label, fill=accent_this)
    content_y += int(38 * _SCALE)
    draw.rectangle([content_x, content_y, content_x + int(56 * _SCALE),
                    content_y + int(5 * _SCALE)], fill=accent_this)
    content_y += int(5 * _SCALE) + int(26 * _SCALE)

    # Title — big, full width
    title_text, f_title, title_h = _fit_text_to_box(
        draw, title, max_w, int(_RH * 0.24), [92, 84, 76, 68, 60], gap=int(12 * _SCALE), max_lines=3
    )
    content_y += _put_text(draw, title_text, f_title, content_x, content_y,
                           max_w, p["title"], gap=12 * _SCALE)
    content_y += int(42 * _SCALE)

    # Body — big, full width, in a softly tinted box with an accent left-edge bar
    body_gap = int(14 * _SCALE)
    box_pad  = int(26 * _SCALE)
    text_w = max_w - box_pad * 2
    available_h = max(0, footer_y - content_y - box_pad - int(28 * _SCALE))
    body_source = body[:260].rsplit(" ", 1)[0] if len(body) > 260 else body
    fitted_body, f_body, _ = _fit_text_to_box(
        draw, body_source, text_w, available_h, [42, 38, 34, 32, 30, 28, 26], gap=body_gap, max_lines=6
    )
    body_lines_h = _text_block_height(draw, fitted_body, f_body, text_w, gap=body_gap)
    box_bg = _mix(bg2, accent_this, 0.13)
    box_top = content_y - box_pad
    box_bot = content_y + body_lines_h + box_pad
    radius = int(16 * _SCALE)
    _draw_rounded_rect(draw, content_x, box_top, content_x + max_w, box_bot, radius, box_bg)
    draw.rectangle([content_x, box_top + radius, content_x + int(7 * _SCALE), box_bot - radius],
                   fill=accent_this)
    _put_text(draw, fitted_body, f_body, content_x + box_pad, content_y, text_w, p["body"], gap=body_gap)

    # ── Cliffhanger teaser: the swipe-completion engine ──
    if teaser:
        f_tease = _font(24 * _SCALE, bold=True)
        tease_txt = f"next: {teaser[:52]}"
        ty2 = footer_y - int(46 * _SCALE)
        draw.text((PAD, ty2), "→", font=f_tease, fill=_a2(p))
        draw.text((PAD + int(34 * _SCALE), ty2), tease_txt, font=f_tease, fill=p["subtitle"])

    # ── Footer: divider + counter (no logo on assets) ──
    draw.rectangle([PAD, footer_y, _RW - PAD, footer_y + int(1 * _SCALE)], fill=p["muted"])
    footer_text_y = footer_y + int(14 * _SCALE)
    if brand:
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


def _slide_context(title: str, body: str, num: int, total: int,
                   brand: str, p: dict, label: str = "START HERE") -> Image.Image:
    """Context slide (slide 2): frames why the reader should keep swiping. No giant numeral."""
    bg2 = _mix(p["bg"], (255, 255, 255), 0.04)
    img = Image.new("RGB", (_RW, _RH), bg2)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, _RW, int(8 * _SCALE)], fill=p["accent"])
    _draw_circle(draw, -int(120 * _SCALE), _RH - int(80 * _SCALE),
                 int(420 * _SCALE), _mix(bg2, p["accent"], 0.09))

    f_label = _font(24 * _SCALE, bold=True)
    f_sm    = _font(21 * _SCALE)
    content_x = PAD
    max_w = _RW - PAD * 2
    footer_y = _RH - int(70 * _SCALE)

    label_text = (label or "START HERE").upper()[:24]
    title_text, f_title, h_title = _fit_text_to_box(
        draw, title, max_w, int(_RH * 0.28), [72, 64, 58, 52, 46], gap=int(12 * _SCALE), max_lines=4)
    body_text, f_body, h_body = _fit_text_to_box(
        draw, body[:300], max_w, int(_RH * 0.32), [36, 34, 32, 30, 28, 26], gap=int(12 * _SCALE), max_lines=7)
    label_h = draw.textbbox((0, 0), label_text, font=f_label)[3]
    gap1, gap2 = int(24 * _SCALE), int(28 * _SCALE)
    block_h = label_h + int(5 * _SCALE) + gap1 + h_title + gap2 + h_body
    y = max(int(110 * _SCALE), (footer_y - block_h) // 2)

    draw.text((content_x, y), label_text, font=f_label, fill=p["accent"])
    y += label_h + int(12 * _SCALE)
    draw.rectangle([content_x, y, content_x + int(60 * _SCALE), y + int(5 * _SCALE)], fill=p["accent"])
    y += int(5 * _SCALE) + gap1
    y += _put_text(draw, title_text, f_title, content_x, y, max_w, p["title"], gap=12 * _SCALE)
    y += gap2
    _put_text(draw, body_text, f_body, content_x, y, max_w, p["body"], gap=12 * _SCALE)

    draw.rectangle([PAD, footer_y, _RW - PAD, footer_y + int(1 * _SCALE)], fill=p["muted"])
    fty = footer_y + int(14 * _SCALE)
    draw.text((PAD, fty), brand, font=f_sm, fill=p["muted"])
    counter = f"{num}/{total}"
    cw = _tw(draw, counter, f_sm)
    draw.text((_RW - PAD - cw, fty), counter, font=f_sm, fill=p["muted"])
    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


def _slide_recap(title: str, bullets: list, num: int, total: int,
                 brand: str, p: dict) -> Image.Image:
    """Recap slide: a screenshot-friendly summary of 3-5 one-line takeaways."""
    bg2 = _mix(p["bg"], (255, 255, 255), 0.04)
    img = Image.new("RGB", (_RW, _RH), bg2)
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, _RW, int(8 * _SCALE)], fill=p["accent"])
    _draw_corner_arc(draw, _RW, 0, int(320 * _SCALE), int(170 * _SCALE),
                     _mix(bg2, p["accent"], 0.20))

    f_label = _font(24 * _SCALE, bold=True)
    f_sm    = _font(21 * _SCALE)
    content_x = PAD
    max_w = _RW - PAD * 2

    top_y = int(96 * _SCALE)
    draw.text((content_x, top_y), "RECAP", font=f_label, fill=p["accent"])
    top_y += int(40 * _SCALE)
    draw.rectangle([content_x, top_y, content_x + int(60 * _SCALE), top_y + int(5 * _SCALE)], fill=p["accent"])
    top_y += int(5 * _SCALE) + int(24 * _SCALE)
    title_text, f_title, th = _fit_text_to_box(
        draw, title or "The key takeaways", max_w, int(_RH * 0.15),
        [60, 54, 48, 44, 40], gap=int(10 * _SCALE), max_lines=2)
    top_y += _put_text(draw, title_text, f_title, content_x, top_y, max_w, p["title"], gap=10 * _SCALE)
    top_y += int(40 * _SCALE)

    bullets = [b for b in (bullets or []) if b][:5]
    footer_y = _RH - int(70 * _SCALE)
    avail_h = max(int(120 * _SCALE), footer_y - top_y - int(24 * _SCALE))
    n = max(1, len(bullets))
    row_h = avail_h // n
    dot_r = int(9 * _SCALE)
    for i, b in enumerate(bullets):
        ry = top_y + i * row_h
        text_x = content_x + dot_r * 2 + int(28 * _SCALE)
        text_w = max_w - (dot_r * 2 + int(28 * _SCALE))
        bt, f_b, bh = _fit_text_to_box(
            draw, b, text_w, row_h - int(18 * _SCALE), [34, 32, 30, 28, 26, 24],
            gap=int(8 * _SCALE), max_lines=2)
        by = ry + max(0, (row_h - bh) // 2)
        _draw_circle(draw, content_x + dot_r, by + int(16 * _SCALE), dot_r, p["accent"])
        _put_text(draw, bt, f_b, text_x, by, text_w, p["body"], gap=8 * _SCALE)
        if i < len(bullets) - 1:
            dy = ry + row_h - int(1 * _SCALE)
            draw.rectangle([content_x, dy, _RW - PAD, dy + int(1 * _SCALE)],
                           fill=_mix(bg2, p["muted"], 0.4))

    draw.rectangle([PAD, footer_y, _RW - PAD, footer_y + int(1 * _SCALE)], fill=p["muted"])
    fty = footer_y + int(14 * _SCALE)
    draw.text((PAD, fty), brand, font=f_sm, fill=p["muted"])
    counter = f"{num}/{total}"
    cw = _tw(draw, counter, f_sm)
    draw.text((_RW - PAD - cw, fty), counter, font=f_sm, fill=p["muted"])
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
                 emphasis: str = "", tag: str = "", rw: int = _RW, rh: int = _RH) -> Image.Image:
    """Standalone branded 'poster' image for a single text+image post.
    Centered composition with a top category tag and an accent-highlighted key phrase —
    deliberately distinct from the left-aligned carousel slides.
    rw/rh are the supersampled render dimensions (default 1:1); pass a taller rh for 4:5."""
    # Diagonal-feel gradient background
    img = Image.new("RGB", (rw, rh), p["bg"])
    _draw_gradient_bg(img, p["bg"], _mix(p["bg"], p["accent"], 0.24))
    draw = ImageDraw.Draw(img)

    # Big soft glow bottom-left + ghost ring top-right for depth
    _draw_circle(draw, -int(140 * _SCALE), rh + int(120 * _SCALE),
                 int(560 * _SCALE), _mix(p["bg"], p["accent"], 0.12))
    _draw_circle(draw, rw + int(120 * _SCALE), -int(120 * _SCALE),
                 int(440 * _SCALE), _mix(p["bg"], p["accent"], 0.10))
    _draw_dot_grid(draw, rw - int(250 * _SCALE), rh - int(170 * _SCALE),
                   5, 3, int(42 * _SCALE), int(5 * _SCALE), _mix(p["bg"], p["accent"], 0.18))

    cx = rw // 2
    pad_x = int(110 * _SCALE)
    max_w = rw - pad_x * 2

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
    region_h = rh - region_top - brand_reserved
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
    by = rh - int(96 * _SCALE)
    _draw_circle(draw, bx + dot_r, by + int(16 * _SCALE), dot_r, p["accent"])
    draw.text((bx + dot_r * 2 + gap, by), brand, font=f_brand, fill=p["subtitle"])

    return img.resize((rw // _SCALE, rh // _SCALE), Image.LANCZOS)


def _slide_tweet_card(text: str, name: str, handle: str, p: dict,
                      rw: int = _RW, rh: int = 0, avatar: Image.Image = None) -> Image.Image:
    """Social-screenshot style, matched to the real genre: pure-black frame CROPPED TO
    CONTENT (dynamic height, like an actual screenshot), regular-weight statement text,
    author row with avatar + verified mark + gray handle, subtle overflow dots top-right.
    Always the AUTHOR's own words and name. rh is ignored — height follows content."""
    pad_x = int(76 * _SCALE)
    inner_w = rw - pad_x * 2
    scratch = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    # Statement: regular weight, sized so lines run ~45 chars like a real tweet screenshot
    # (bigger text reads as "designed graphic"; smaller-with-air reads as "captured post")
    body_gap = int(16 * _SCALE)
    para_gap = int(38 * _SCALE)
    paras = [s.strip() for s in re.split(r"\n\s*\n|\n", text or "") if s.strip()] or [""]
    joined = " ".join(paras)
    sizes = [41, 39, 37, 35, 33]
    f_body = None
    for s in sizes:
        f_try = _font(s * _SCALE)
        total = sum(_wrap_height(scratch, _wrap(scratch, pa, f_try, inner_w), f_try, body_gap) for pa in paras)
        if total <= int(1100 * _SCALE) and len(_wrap(scratch, joined, f_try, inner_w)) <= 12:
            f_body = f_try
            break
    f_body = f_body or _font(sizes[-1] * _SCALE)
    para_lines = [_wrap(scratch, pa, f_body, inner_w) for pa in paras]
    h_body = sum(_wrap_height(scratch, ls, f_body, body_gap) for ls in para_lines)
    h_body += para_gap * max(0, len(para_lines) - 1)

    # Screenshot-crop dimensions: height follows content
    av_r = int(40 * _SCALE)
    head_h = av_r * 2
    gap_head_body = int(48 * _SCALE)
    top = int(80 * _SCALE)
    bottom = int(88 * _SCALE)
    rh_dyn = top + head_h + gap_head_body + h_body + bottom

    img = Image.new("RGB", (rw, rh_dyn), (5, 5, 6))
    draw = ImageDraw.Draw(img)

    # Author row
    ax, ay = pad_x, top
    if avatar is not None:
        av = avatar.convert("RGB").resize((av_r * 2, av_r * 2), Image.LANCZOS)
        mask = Image.new("L", (av_r * 2, av_r * 2), 0)
        ImageDraw.Draw(mask).ellipse([0, 0, av_r * 2, av_r * 2], fill=255)
        img.paste(av, (ax, ay), mask)
    else:
        _draw_circle(draw, ax + av_r, ay + av_r, av_r, tuple(p["accent"]))
        initial = (name or "V")[0].upper()
        f_init = _font(40 * _SCALE, bold=True)
        iw = _tw(draw, initial, f_init)
        ih = draw.textbbox((0, 0), initial, font=f_init)[3]
        draw.text((ax + av_r - iw // 2, ay + av_r - ih // 2 - int(4 * _SCALE)), initial,
                  font=f_init, fill=(255, 255, 255))

    f_name = _font(34 * _SCALE, bold=True)
    f_handle = _font(29 * _SCALE)
    tx = ax + av_r * 2 + int(26 * _SCALE)
    name_txt = (name or "")[:28]
    draw.text((tx, ay + int(2 * _SCALE)), name_txt, font=f_name, fill=(255, 255, 255))
    nw = _tw(draw, name_txt, f_name)
    # Verified seal: the scalloped 8-lobed starburst, not a plain circle
    vb_r = int(17 * _SCALE)
    vb_cx = tx + nw + int(18 * _SCALE) + vb_r
    vb_cy = ay + int(2 * _SCALE) + draw.textbbox((0, 0), name_txt, font=f_name)[3] // 2 + int(3 * _SCALE)
    import math
    # Soft rosette: 8 overlapping petal circles + core — matches the real seal's rounded lobes
    blue = (29, 155, 240)
    petal_r = vb_r * 0.50
    orbit = vb_r * 0.56
    for i in range(8):
        ang = math.pi * i / 4 + math.pi / 8
        px = vb_cx + orbit * math.cos(ang)
        py = vb_cy + orbit * math.sin(ang)
        _draw_circle(draw, int(px), int(py), int(petal_r), blue)
    _draw_circle(draw, vb_cx, vb_cy, int(vb_r * 0.88), blue)
    ck = int(vb_r * 0.48)
    draw.line([(vb_cx - ck, vb_cy + int(1 * _SCALE)), (vb_cx - ck // 3, vb_cy + ck * 2 // 3),
               (vb_cx + ck, vb_cy - ck // 2)], fill=(255, 255, 255),
              width=max(4, int(vb_r * 0.28)), joint="curve")
    draw.text((tx, ay + int(50 * _SCALE)), (handle or "")[:32], font=f_handle, fill=(113, 118, 123))

    # Overflow dots top-right (subtle platform chrome)
    dots_y = ay + int(18 * _SCALE)
    for i in range(3):
        _draw_circle(draw, rw - pad_x - i * int(16 * _SCALE), dots_y, int(3.5 * _SCALE), (113, 118, 123))

    # The statement
    y = top + head_h + gap_head_body
    for pi, lines in enumerate(para_lines):
        for line in lines:
            draw.text((pad_x, y), line, font=f_body, fill=(231, 233, 234))
            y += draw.textbbox((0, 0), line, font=f_body)[3] + body_gap
        if pi < len(para_lines) - 1:
            y += para_gap - body_gap

    return img.resize((rw // _SCALE, rh_dyn // _SCALE), Image.LANCZOS)


def _slide_list_card(tag: str, headline: str, emphasis: str, points: list, brand: str, p: dict,
                     rw: int = _RW, rh: int = _RH) -> Image.Image:
    """Branded 'framework' card: headline + numbered value points. The information-dense
    counterpart to _slide_quote — for posts whose payload is a list, steps, or rules."""
    img = Image.new("RGB", (rw, rh), p["bg"])
    _draw_gradient_bg(img, p["bg"], _mix(p["bg"], p["accent"], 0.22))
    draw = ImageDraw.Draw(img)
    _draw_circle(draw, -int(140 * _SCALE), rh + int(120 * _SCALE),
                 int(520 * _SCALE), _mix(p["bg"], p["accent"], 0.10))
    _draw_dot_grid(draw, rw - int(250 * _SCALE), int(120 * _SCALE),
                   5, 3, int(42 * _SCALE), int(5 * _SCALE), _mix(p["bg"], p["accent"], 0.18))

    cx = rw // 2
    pad_x = int(100 * _SCALE)
    max_w = rw - pad_x * 2

    # Top pill tag
    tag = (tag or "").strip().upper()[:22]
    y = int(110 * _SCALE)
    if tag:
        f_tag = _font(28 * _SCALE, bold=True)
        tw = _tw(draw, tag, f_tag)
        ph, pv = int(26 * _SCALE), int(13 * _SCALE)
        th = draw.textbbox((0, 0), tag, font=f_tag)[3]
        px = cx - (tw + ph * 2) // 2
        _draw_rounded_rect(draw, px, y, px + tw + ph * 2, y + th + pv * 2, (th + pv * 2) // 2,
                           _mix(p["bg"], p["accent"], 0.28))
        draw.text((px + ph, y + pv), tag, font=f_tag, fill=p["accent"])
        y += th + pv * 2 + int(46 * _SCALE)

    # Measure everything first, then vertically center the whole block (headline + points)
    head_gap = int(12 * _SCALE)
    hook_text, f_head, _ = _fit_text_to_box(
        draw, headline, max_w, int(rh * 0.24), [72, 64, 58, 52, 46], gap=head_gap, max_lines=3)
    h_head = _wrap_height(draw, _wrap(draw, hook_text, f_head, max_w), f_head, head_gap)
    gap_after_head = int(56 * _SCALE)

    pts = [str(pt).strip() for pt in (points or []) if str(pt).strip()][:4]
    f_num = _font(40 * _SCALE, bold=True)
    body_sizes = [40, 38, 36, 34] if len(pts) <= 3 else [36, 34, 32, 30]
    item_gap = int(8 * _SCALE)
    item_spacing = int(44 * _SCALE)
    bottom_limit = rh - int(150 * _SCALE)
    num_w = _tw(draw, "01", f_num) + int(28 * _SCALE)
    text_w = max_w - num_w

    items = []
    for pt in pts:
        body, f_body, _ = _fit_text_to_box(
            draw, pt, text_w, int(rh * 0.14), body_sizes, gap=item_gap, max_lines=3)
        lines = _wrap(draw, body, f_body, text_w)
        items.append((lines, f_body, _wrap_height(draw, lines, f_body, item_gap)))

    total_h = h_head + gap_after_head + sum(h for _, _, h in items) + item_spacing * max(0, len(items) - 1)
    y += max(0, (bottom_limit - y - total_h) // 2)

    y = _draw_centered_emphasis(draw, hook_text, f_head, cx, y, max_w,
                                p["title"], p["accent"], emphasis, head_gap)
    y += gap_after_head - head_gap

    for i, (lines, f_body, item_h) in enumerate(items):
        if y + item_h > bottom_limit:
            break
        draw.text((pad_x, y), f"{i + 1:02d}", font=f_num, fill=p["accent"])
        ty = y
        for line in lines:
            draw.text((pad_x + num_w, ty), line, font=f_body, fill=p["body"])
            ty += draw.textbbox((0, 0), line, font=f_body)[3] + item_gap
        y += item_h + item_spacing
        if i < len(items) - 1:
            draw.rectangle([pad_x + num_w, y - int(22 * _SCALE), rw - pad_x, y - int(22 * _SCALE) + int(2 * _SCALE)],
                           fill=_mix(p["bg"], p["accent"], 0.16))

    # Brand bottom-center
    f_brand = _font(26 * _SCALE, bold=True)
    bw = _tw(draw, brand, f_brand)
    dot_r = int(7 * _SCALE)
    gap = int(14 * _SCALE)
    bx = cx - (dot_r * 2 + gap + bw) // 2
    by = rh - int(96 * _SCALE)
    _draw_circle(draw, bx + dot_r, by + int(16 * _SCALE), dot_r, p["accent"])
    draw.text((bx + dot_r * 2 + gap, by), brand, font=f_brand, fill=p["subtitle"])

    return img.resize((rw // _SCALE, rh // _SCALE), Image.LANCZOS)


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
post_text: the LinkedIn caption. Hook first (max 60 characters, self-contained, open loop),
  no warm-up. 60-120 words total. Plain words a 15-year-old uses daily, most sentences under
  12 words. 2-3 short paragraphs, varied rhythm. 0-3 lowercase hashtags on the last line.

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


# Image posts render as vertical 4:5 (1080x1350) — the tallest ratio LinkedIn shows
# in full before cropping, which maximizes mobile feed real estate (research-backed).
IMG_POST_W, IMG_POST_H = 1080, 1350


def render_image_post_png(content: dict, company: dict) -> bytes:
    """Render the branded 'insight card' image post at 4:5."""
    p = _get_palette(company)
    brand = (company or {}).get("name", "Voyce")
    img = _slide_quote(
        content.get("card_headline", ""), content.get("card_subtext", ""), brand, p,
        emphasis=content.get("card_emphasis", ""), tag=content.get("card_tag", ""),
        rw=IMG_POST_W * _SCALE, rh=IMG_POST_H * _SCALE,
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── AI illustration image post (research-grounded) ───────────────────────────
# Research (deep-research, 2026): for founders, AI images work ONLY when they read as
# intentionally-designed flat/editorial graphics — never photorealistic, never AI human
# faces/hands (the #1 credibility killer and "AI slop" tell), never model-rendered text
# (garbles). So we generate a clean, muted, flat illustration and add any text ourselves.

_AI_IMAGE_SYSTEM = """You design the VISUAL for a founder's LinkedIn post, plus the caption.

FORMAT — decide this FIRST. Images on LinkedIn earn attention by carrying INFORMATION, not decoration:
- "list_card": the post's payload is a list, framework, steps, rules, or lessons → a designed
  card with the headline + 2-4 numbered points (fill card_points). DEFAULT for abstract
  professional topics with enumerable substance.
- "quote_card": the post's power is ONE sharp quotable claim → a designed poster of that line
  (fill card_headline/card_emphasis/card_subtext). Use when there's a killer one-liner but no list.
- "tweet_card": the claim is a HOT TAKE — contrarian, spicy, a little confrontational → a
  social-screenshot style card of that line (fill card_headline with the take, max ~30 words).
  Reads as a captured opinion, not marketing. Use for the boldest, most argument-starting claims.
- "scene": ONLY for tangible, physical, visual topics (farming, logistics, food, manufacturing,
  travel...) where a real scene from the post's world adds feeling that text cannot. A decorative
  metaphor for an abstract topic is NOT a scene — abstract topics take a card.

Return JSON:
image_concept: FIRST boil the post down to its ONE core message in a short phrase (e.g. "build a button,
  not a platform", "talk to users instead of just coding", "focus on a single channel"). THEN describe,
  in 2-3 sentences for an illustrator, a SIMPLE, bold, iconic visual metaphor a reader would INSTANTLY
  connect to THAT message — a stranger seeing the image beside the post should think "yes, that fits."
  Compose it like a New Yorker / Monocle / Stripe-press editorial illustration: ONE clear focal subject,
  a few supporting shapes at most, generous negative space. Conceptual, a little clever, concrete, ON-TOPIC.
  Example — post about "talk to your users, don't just build": "A lone office chair turned away from a
  softly glowing desk lamp to face a single empty chair across a small round table."
  Example — post about "ship one simple thing, not a sprawling platform": "A single oversized, satisfying
  round button resting alone on a clean pedestal, dwarfing a small tangled heap of disconnected parts
  pushed to the edge of the frame."
  LITERAL vs METAPHOR — choose deliberately:
  - If the post is about TANGIBLE, PHYSICAL work (farming, logistics, food, manufacturing,
    construction, retail, healthcare...), paint a REAL SCENE from that world: its actual places,
    tools, vehicles, crops, materials. A post about farmers wants a field at dawn or a loaded
    truck on a wet rural road, NOT a clever object metaphor. Literal, specific, alive.
  - Reserve conceptual metaphors for ABSTRACT topics (strategy, mindset, code, money, careers)
    where there is nothing physical to show.
  - If an AUTHOR'S WORLD is given, set the scene inside that world.
  HARD RULES:
  - The metaphor MUST relate to the post's actual subject. If you can't say in one line why it fits, pick another.
  - KEEP IT SIMPLE AND TEXT-FREE. The illustration tool scrawls gibberish letters onto anything that
    normally carries text or dense repeated detail — so NEVER feature: screens / monitors / phones showing a
    UI, dashboards, spreadsheets, charts or graphs, keyboards, circuit boards or breadboards, books,
    newspapers, documents, letters, signs, price tags, or labelled buttons/keys. Pick clean, textless
    objects and shapes instead, and keep the scene uncluttered (a few big elements, not a busy detailed one).
  - Do NOT produce decorative or unrelated scenery — no temples, monuments, landmarks, palaces, cityscapes,
    landscapes, or pretty buildings — unless the post is literally about that.
  - BAN tired clichés: lightbulbs, handshakes, chess pieces, gears, rockets, ladders, mountains-with-a-flag,
    two cups, glowing brains, generic upward arrows.
  - NEVER include text, numbers, letters, logos, brand names, charts with labels, or human faces.
alt_text: a short accessibility description of the image (<= 100 chars).
CAPTION RULE: the image carries the CLAIM; post_text makes the CASE. post_text must NOT open
with or repeat the card_headline/key_line — it expands the claim with a story, example, or
specifics, and may end with one genuine question. Card and caption together, never twice the same line.
post_text: the LinkedIn caption. First line max 60 characters, self-contained, open loop, no
  warm-up. 60-120 words total, plain words a 15-year-old uses daily, most sentences under 12
  words, varied rhythm. 0-3 lowercase hashtags on the final line. Plain text only, no markdown.

VOICE: if a voice/author profile is provided, write the caption the way that author talks.
FACTUAL SAFETY: never invent exact stats, product versions, dates, or named studies.
NEVER USE: "It's not X. It's Y.", "Let that sink in", game-changer, unlock, leverage, synergy.

Return ONLY JSON: {"image_concept": "...", "alt_text": "...", "post_text": "..."}"""


_HUE_NAMES = [
    (18, "red"), (42, "warm orange"), (58, "amber"), (72, "gold"),
    (95, "lime green"), (150, "green"), (175, "teal"), (200, "cyan"),
    (240, "blue"), (268, "indigo"), (288, "violet"), (318, "purple"),
    (338, "magenta"), (360, "red"),
]


def _describe_color(rgb) -> str:
    """A human colour name for an RGB tuple, so the image model uses the real palette."""
    r, g, b = (c / 255 for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    if s < 0.16:  # near-neutral
        if v > 0.90: return "off-white"
        if v > 0.72: return "warm cream"
        if v > 0.48: return "warm grey"
        if v > 0.26: return "dark charcoal"
        return "near-black"
    deg = h * 360
    name = next((nm for lim, nm in _HUE_NAMES if deg <= lim), "blue")
    return ("deep " + name) if v < 0.45 else name


def _palette_words(p: dict) -> str:
    def _hx(c): return "#%02x%02x%02x" % (int(c[0]), int(c[1]), int(c[2]))
    bg_name = _describe_color(p["bg"])
    accent_name = _describe_color(p["accent"])
    return (f"a restrained 2-3 colour palette: a {bg_name} background (about {_hx(p['bg'])}), "
            f"a single {accent_name} accent colour (about {_hx(p['accent'])}) used sparingly, "
            "and a neutral ink tone for linework — muted and tasteful, not saturated")


def _build_ai_image_prompt(concept: str, p: dict) -> str:
    return (
        f"Minimalist flat editorial illustration for a premium magazine cover. Concept: {concept}. "
        f"Style: {_palette_words(p)}. Bold, simple flat vector shapes with subtle grain and soft flat "
        "shading, clean confident linework, ONE clear focal subject with generous negative space — keep "
        "it simple and uncluttered, only a few large elements, NOT busy or finely detailed. High contrast, "
        "tasteful, restrained, conceptual — like Stripe Press, Monocle, or a New Yorker cover. "
        "CRITICAL: absolutely NO text, NO words, NO letters, NO numbers, NO gibberish writing, NO labels, "
        "NO signs, NO logos, NO watermark, NO charts or graphs. Do NOT draw screens or phones with "
        "interfaces, keyboards, circuit boards, books or documents, or anything that would carry writing. "
        "NO close-up faces or detailed hands (small, distant human figures whose faces aren't visible are "
        "fine and add life to a scene). NO photorealism, NO 3D render, NO glossy corporate stock "
        "look, NO cliche startup imagery. Vertical 4:5 poster composition."
    )


def _cover_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    """Scale + center-crop to exactly w x h (cover fit)."""
    img = img.convert("RGB")
    sw, sh = img.size
    scale = max(w / sw, h / sh)
    resized = img.resize((max(1, round(sw * scale)), max(1, round(sh * scale))), Image.LANCZOS)
    nw, nh = resized.size
    left, top = (nw - w) // 2, (nh - h) // 2
    return resized.crop((left, top, left + w, top + h))


def _with_brand_mark(img: Image.Image, brand: str, p: dict) -> Image.Image:
    """Small, tasteful brand wordmark bottom-left on a translucent chip for legibility."""
    base = img.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    f = _font(26, bold=True)
    label = (brand or "Voyce")[:24]
    tw = _tw(d, label, f)
    th = d.textbbox((0, 0), label, font=f)[3]
    pad, dot_r = 14, 5
    chip_w = pad + dot_r * 2 + 10 + tw + pad
    chip_h = th + pad * 2
    x = 32
    y = base.size[1] - chip_h - 32
    d.rounded_rectangle([x, y, x + chip_w, y + chip_h], radius=chip_h // 2, fill=(0, 0, 0, 120))
    cy = y + chip_h // 2
    d.ellipse([x + pad, cy - dot_r, x + pad + dot_r * 2, cy + dot_r], fill=tuple(p["accent"]) + (255,))
    d.text((x + pad + dot_r * 2 + 10, y + pad), label, font=f, fill=(255, 255, 255, 255))
    return Image.alpha_composite(base, overlay).convert("RGB")


def generate_ai_image_post(raw_text: str, company: dict = None) -> dict:
    """LLM: pick the strongest idea → a concrete visual concept + caption for an AI illustration."""
    from generator import _strip_markdown
    voice_section = ""
    if company:
        from generator import _build_voice_block
        vb = _build_voice_block(company)
        if vb:
            voice_section = f"\n\nVOICE PROFILE:\n{vb[:1200]}"
    industry = (company or {}).get("industry", "").strip()
    world = f"\nAUTHOR'S WORLD: {industry} — set the visual inside this world." if industry else ""
    prompt = f"""Content to turn into ONE editorial illustration + caption:

{raw_text[:2000]}{voice_section}{world}

Choose the single strongest idea and design the visual for it (a real scene from the post's
world if the topic is physical/tangible; a concrete metaphor only if the topic is abstract).

RELEVANCE GATE: before answering, check your concept against the post's core claim. A reader
scrolling past must connect the image to the post's point INSTANTLY, with zero explanation.
If the connection needs a caption to make sense, discard the concept and pick a more literal one.

Also return "key_line": the single most quotable line from the post itself (max 12 words,
verbatim or lightly trimmed) — punchy enough to stand alone on a typographic card.

Return ONLY valid JSON:
{{"format": "list_card|quote_card|tweet_card|scene", "image_concept": "... (scene only)",
"card_tag": "...", "card_headline": "...", "card_emphasis": "...", "card_subtext": "...",
"card_points": ["...", "..."], "alt_text": "...", "key_line": "...", "post_text": "..."}}"""
    result = generate_json(prompt, system=_AI_IMAGE_SYSTEM, max_tokens=1600, temperature=0.85)
    fmt = (result.get("format") or "").strip().lower()
    if fmt not in ("scene", "quote_card", "list_card", "tweet_card"):
        fmt = "scene" if (result.get("image_concept") or "").strip() else "quote_card"
    return {
        "format":        fmt,
        "image_concept": (result.get("image_concept") or "").strip(),
        "card_tag":      (result.get("card_tag") or "").strip(),
        "card_headline": (result.get("card_headline") or "").strip(),
        "card_emphasis": (result.get("card_emphasis") or "").strip(),
        "card_subtext":  (result.get("card_subtext") or "").strip(),
        "card_points":   [str(x).strip() for x in (result.get("card_points") or []) if str(x).strip()][:4],
        "alt_text":      (result.get("alt_text") or "").strip(),
        "key_line":      (result.get("key_line") or "").strip(),
        "post_text":     _strip_markdown(result.get("post_text", "") or ""),
    }


def render_ai_image_png(content: dict, company: dict) -> bytes:
    """Generate a flat editorial illustration for the concept, composed to 4:5 with a small
    brand wordmark. Falls back to the branded insight card if image generation is unavailable."""
    from llm import generate_image
    p = _get_palette(company)
    brand = (company or {}).get("name", "Voyce")
    concept = (content.get("image_concept") or "").strip()
    fmt = (content.get("format") or "scene").strip().lower()

    # Designed value cards: information over decoration. Rendered by us — always crisp, always relevant.
    if fmt == "list_card" and content.get("card_points"):
        img = _slide_list_card(
            content.get("card_tag", ""), content.get("card_headline") or content.get("key_line", ""),
            content.get("card_emphasis", ""), content.get("card_points"), brand, p,
            rw=IMG_POST_W * _SCALE, rh=IMG_POST_H * _SCALE)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    if fmt == "tweet_card" and (content.get("card_headline") or content.get("key_line")):
        handle = "@" + re.sub(r"[^a-z0-9]+", "", (brand or "voyce").lower())[:24]
        # Real photo avatar by convention: backend/assets/avatars/<name-slug>.png
        avatar_img = None
        try:
            slug = re.sub(r"[^a-z0-9]+", "-", (brand or "").lower()).strip("-")
            av_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "avatars", f"{slug}.png")
            if slug and os.path.exists(av_path):
                avatar_img = Image.open(av_path)
        except Exception:
            avatar_img = None
        img = _slide_tweet_card(
            content.get("card_headline") or content.get("key_line", ""), brand, handle, p,
            rw=IMG_POST_W * _SCALE, avatar=avatar_img)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    if fmt == "quote_card" and (content.get("card_headline") or content.get("key_line")):
        card = {
            "card_headline": content.get("card_headline") or content.get("key_line", ""),
            "card_subtext": content.get("card_subtext", ""),
            "card_emphasis": content.get("card_emphasis", ""),
            "card_tag": content.get("card_tag", ""),
        }
        return render_image_post_png(card, company)

    # Transient API failures are common enough to deserve one retry before falling back.
    img_bytes = None
    if concept:
        rendered_prompt = _build_ai_image_prompt(concept, p)
        for _ in range(2):
            img_bytes = generate_image(rendered_prompt)
            if img_bytes:
                break
    if img_bytes:
        try:
            canvas = _cover_crop(Image.open(io.BytesIO(img_bytes)), IMG_POST_W, IMG_POST_H)
            buf = io.BytesIO()
            canvas.save(buf, format="PNG")
            return buf.getvalue()
        except Exception:
            pass

    # Graceful fallback: the branded insight card. Its headline must be a line FROM THE POST
    # (key_line), never alt_text — alt_text is a scene description and reads as nonsense on a card.
    post_text = (content.get("post_text") or "").strip()
    first_sentence = re.split(r"(?<=[.!?])\s+", post_text)[0][:90] if post_text else ""
    card = {
        "card_headline": content.get("card_headline") or content.get("key_line") or first_sentence or brand,
        "card_subtext": "", "card_emphasis": "", "card_tag": "",
    }
    return render_image_post_png(card, company)


def generate_caption_from_text(raw_text: str, company: dict = None) -> dict:
    """Caption + alt text for a user-uploaded image (no image generated)."""
    data = generate_ai_image_post(raw_text, company)
    return {"post_text": data.get("post_text", ""), "alt_text": data.get("alt_text", "")}


# ── Manual carousel (from pasted content) ────────────────────────────────────

_CAROUSEL_SYSTEM = """You are a world-class LinkedIn carousel strategist for founders and creators.

GOAL — SAVE-WORTHY: carousels win because people SAVE them, and saves drive reach far more than
likes. Build a keepable reference: a step-by-step playbook, a numbered framework, or a checklist —
something a reader thinks "I need this later." These formats earn the most saves; prefer them.

DECK STRUCTURE — a five-part narrative, follow it exactly:
1. hook_slide     — the cover. The single most important slide.
2. context_slide  — slide 2: answers "why should I keep swiping?" Name the stakes/pain in 1-2 lines.
3. content_slides — 5 to 8 slides, ONE idea per slide, in a logical progression.
4. recap_slide    — 3 to 5 one-line takeaways the reader can screenshot.
5. cta_slide      — a single, specific next action.
Total deck = 9 to 12 slides. Never fewer than 8, never more than 12.

ONE IDEA PER SLIDE (the rule most people break):
- Each content slide makes exactly one point. If a slide has two ideas, split it.
- Keep every slide's body under ~40 words. Short, high-contrast, skimmable on a phone.

HOOK / COVER SLIDE (80% of success) — headline uses ONE of these proven formulas:
- Contrarian:         "Stop doing X. Do Y instead."
- Curiosity + pain:   "X mistakes quietly killing your Y"
- Result + timeframe: "How I did X in Y days/weeks"
- Framework promise:  "The X framework that got Y"
- Data authority:     "I analyzed X. Here's what wins."
Make it specific (use a real number/name where you can). NEVER generic titles like "Tips for X",
"X Is Important", "Why X Matters", "The Future of X". subtext adds a fact or the promise — never
restates the headline.

CONTENT SLIDES — each has a "kind":
- "point" (default): title = the point as a bold claim (max 6 words); body = ONE concrete example,
  company, number, or step (max ~40 words). label = a short tag ("STEP 1", "MISTAKE 2", "MYTH",
  "TRUTH") fitting the arc.
- "stat": use when one number IS the story. stat = the number ("43%", "3x", "₹2.4 Cr"); title =
  what it means; body = one line of context with the source. Use at most one stat slide.

RECAP SLIDE: title = a short recap heading; bullets = 3-5 punchy one-liners summarizing the deck.

CTA SLIDE: headline = the ONE thing to remember (a declaration). cta = a SINGLE specific ask tied
to the topic ("Save this for your next launch", "DM me PLAYBOOK for the template", or a genuine
question). NEVER engagement bait ("Comment YES", "Like if you agree", "Tag a friend") — LinkedIn
penalizes it. Never stack multiple asks.

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

post_text: the LinkedIn caption. Hook first (max 60 characters, self-contained, open loop),
no warm-up. 60-120 words total, plain words a 15-year-old uses daily, most sentences under
12 words, varied rhythm. 0-3 lowercase hashtags on the last line, only if relevant.

Return ONLY valid JSON."""

_CAROUSEL_SCHEMA = """{
  "hook_slide": {"headline": "...", "subtext": "..."},
  "context_slide": {"title": "...", "body": "..."},
  "content_slides": [
    {"kind": "point", "label": "STEP 1", "title": "...", "body": "..."},
    {"kind": "stat", "label": "THE NUMBER", "stat": "43%", "title": "...", "body": "..."},
    {"kind": "point", "label": "STEP 2", "title": "...", "body": "..."}
  ],
  "recap_slide": {"title": "The recap", "bullets": ["...", "...", "..."]},
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

    ctx = result.get("context_slide") or {}
    ctx_title = _clean_slide_text(ctx.get("title", "")) if ctx.get("title") else ""
    ctx_body = _clean_slide_text(ctx.get("body", "")) if ctx.get("body") else ""
    result["context_slide"] = {"title": ctx_title, "body": ctx_body} if (ctx_title or ctx_body) else None

    cleaned_content = []
    for s in (result.get("content_slides") or []):
        s = s or {}
        kind = s.get("kind", "point")
        cleaned = {
            "kind":  kind if kind in ("point", "stat") else "point",
            "label": _clean_slide_text(s.get("label", ""))[:24] if s.get("label") else "",
            "title": _clean_slide_text(s.get("title", "")) if s.get("title") else "",
            "body":  _clean_slide_text(s.get("body", "")) if s.get("body") else "",
        }
        if cleaned["kind"] == "stat":
            cleaned["stat"] = _clean_slide_text(s.get("stat", ""))[:14] if s.get("stat") else ""
            if not cleaned["stat"]:
                cleaned["kind"] = "point"
        if cleaned["title"] or cleaned["body"] or cleaned.get("stat"):
            cleaned_content.append(cleaned)
    result["content_slides"] = cleaned_content[:8]

    recap = result.get("recap_slide") or {}
    recap_bullets = [_clean_slide_text(b) for b in (recap.get("bullets") or []) if b and str(b).strip()][:5]
    result["recap_slide"] = ({
        "title": (_clean_slide_text(recap.get("title", "")) if recap.get("title") else "") or "The recap",
        "bullets": recap_bullets,
    } if recap_bullets else None)

    cta = result.get("cta_slide") or {}
    cta["headline"] = _clean_slide_text(cta.get("headline", ""))
    cta["cta"] = _clean_slide_text(cta.get("cta", ""))
    result["cta_slide"] = cta

    if "post_text" in result:
        from generator import _strip_markdown
        result["post_text"] = _strip_markdown(result.get("post_text") or "")
    return result


_SLIDE_CONSTRAINTS = """Slide constraints (follow exactly):
- hook_slide (cover): headline uses ONE proven formula, max 10 words, specific. subtext: one line, max 14 words.
- context_slide: title (max 8 words) + body (max ~35 words) that makes the reader want to keep swiping.
- content_slides: 5-8 slides, ONE idea each. title max 6 words as a claim; body max ~40 words with a real specific.
- recap_slide: title + 3-5 one-line bullets (each max ~12 words).
- cta_slide: headline (the one takeaway) + a SINGLE specific ask (no engagement bait).
Aim for 9-12 total slides. Prefer a step-by-step playbook, numbered framework, or checklist."""


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

{_SLIDE_CONSTRAINTS}

Return ONLY valid JSON in this shape:
{_CAROUSEL_SCHEMA}"""

    result = generate_json(prompt, system=_CAROUSEL_SYSTEM, max_tokens=6000, temperature=0.85)
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

{_SLIDE_CONSTRAINTS}
Content-slide bodies must cite real companies, reports, or numbers — nothing that could apply to any industry.

{voice_note}
{_hook_guidance(company.get("allowed_hooks"))}

Return ONLY valid JSON in this shape:
{_CAROUSEL_SCHEMA}"""

    result = generate_json(prompt, system=_CAROUSEL_SYSTEM, max_tokens=6000, temperature=0.85)
    return _sanitize_carousel_result(result)


# ── PDF assembly ──────────────────────────────────────────────────────────────

def render_carousel_pdf(content: dict, company: dict) -> bytes:
    brand    = ""  # keep exported assets clean — no logo/watermark (user preference)
    hook     = content["hook_slide"]
    context  = content.get("context_slide")
    c_slides = content.get("content_slides", [])
    recap    = content.get("recap_slide")
    cta      = content["cta_slide"]

    total = 1 + (1 if context else 0) + len(c_slides) + (1 if recap else 0) + 1
    p = _get_palette(company)

    name = (company or {}).get("name", "")
    # Cover rotation: number-led decks alternate between the editorial cover and the
    # high-energy number-slab, so consecutive carousels never wear the same face.
    n_txt, n_rest = _split_number_hook(hook["headline"])
    use_slab = bool(n_txt) and (len(hook["headline"]) % 2 == 0)
    if use_slab:
        slides = [_slide_hook_number_block(n_txt, n_rest, "~2 min read · save it",
                                           hook.get("subtext", ""), total, p, brand=name)]
    else:
        slides = [_slide_hook_editorial(hook["headline"], hook["subtext"], total, p, brand=name,
                                        n_steps=len(c_slides))]
    num = 2
    if context:
        first_teaser = c_slides[0].get("title", "") if c_slides else ""
        slides.append(_slide_body_v3(context["title"], context["body"], num, total, name, p,
                                     label="START HERE", teaser=first_teaser))
        num += 1
    for i, s in enumerate(c_slides):
        # Auto-teaser from the NEXT slide's title — every swipe leaves an open loop
        nxt = c_slides[i + 1].get("title", "") if i + 1 < len(c_slides) else ("the recap" if recap else "")
        if s.get("kind") == "stat" and s.get("stat"):
            slides.append(_slide_stat_v3(s["stat"], s.get("title", ""), s.get("body", ""),
                                         num, total, name, p, teaser=nxt))
        else:
            slides.append(_slide_body_v3(s.get("title", ""), s.get("body", ""), num, total,
                                         name, p, label=s.get("label", ""), step=i + 1,
                                         teaser=nxt))
        num += 1
    if recap:
        slides.append(_slide_recap_v3(recap["title"], recap["bullets"], num, total, name, p))
        num += 1
    slides.append(_slide_cta_v3(cta["headline"], cta["cta"], total, name, p))

    buf = io.BytesIO()
    slides[0].save(buf, format="PDF", save_all=True, append_images=slides[1:], resolution=300)
    return buf.getvalue()
