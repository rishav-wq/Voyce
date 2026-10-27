import colorsys
import io
import logging
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
    "warm_violet": {                    # Voyce brand — warm cream + deep navy (default).
                                        # Key kept as-is so saved profiles don't break.
        "bg":       (244, 240, 232),   # #f4f0e8 warm beige canvas
        "accent":   (36,  54,  94),    # #24365e deep navy
        "title":    (28,  24,  19),    # #1c1813 warm black
        "subtitle": (92,  86,  78),    # muted warm grey
        "body":     (60,  56,  50),    # dark warm grey
        "muted":    (196, 188, 174),   # light warm grey
    },
    "fintech_navy": {                   # finance/tax brands — deep navy + azure
        "bg":       (7,   16,  32),    # #071020 deep navy
        "accent":   (0,   166, 231),   # #00a6e7 azure
        "title":    (255, 255, 255),
        "subtitle": (148, 176, 200),   # #94b0c8 blued grey
        "body":     (205, 220, 235),   # #cddceb
        "muted":    (52,  74,  100),   # #344a64
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
    "warm_violet": (200, 150, 44),    # cream/navy + warm brass
    "fintech_navy": (251, 191, 36),   # navy/azure + amber
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
    "finance":       "fintech_navy",
    "fintech":       "fintech_navy",
    "tax":           "fintech_navy",
    "banking":       "fintech_navy",
    "insurance":     "fintech_navy",
    "consulting":    "warm_dark",
    "legal":         "warm_dark",
    "accounting":    "fintech_navy",
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


def _palette_from_spec(spec: dict) -> dict | None:
    """An optional stored theme_spec (a profile's own palette) in the renderer's
    palette vocabulary. Every color is re-parsed defensively — a stored spec is
    data, not trusted config — and any gap voids the spec entirely so we fall
    through to the normal chain rather than render a half-themed deck."""
    palette = (spec or {}).get("palette") or {}
    out = {}
    for key in ("bg", "accent", "title", "subtitle", "body", "muted"):
        rgb = _hex_to_rgb(str(palette.get(key, "")))
        if not rgb:
            return None
        out[key] = rgb
    accent2 = _hex_to_rgb(str(palette.get("accent2", "")))
    out["accent2"] = accent2 or _ACCENT2.get("warm_violet", (163, 230, 53))
    return out


def _get_palette(company: dict) -> dict:
    """
    Priority: a profile's stored theme_spec → scraped brand_color → explicit
    carousel_theme → industry keyword → default.
    """
    # 0. A stored theme spec wins outright — it exists precisely to brand this
    # profile's decks with a full, contrast-checked palette.
    spec_palette = _palette_from_spec(company.get("theme_spec") or {})
    if spec_palette:
        return spec_palette

    # 1. Scraped brand color overrides the named/keyword themes
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


_INK_V3 = (23, 21, 31)
_CREAM_V3 = (244, 240, 232)


# ── Brand lockup helpers ────────────────────────────────────────────────────────


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


def _fit_one_line(draw, text: str, font, max_w: int) -> str:
    """One line that fits max_w: whole when it fits, else ended at a whole word with "…"
    (never on a dangling "of", "and", "|")."""
    t = " ".join((text or "").split())
    if _tw(draw, t, font) <= max_w:
        return t
    words = t.split()
    while words and _tw(draw, " ".join(words) + "…", font) > max_w:
        words.pop()
    while words and (words[-1].lower() in {"of", "at", "and", "&", "the", "for", "in", "to", "|", "-", "–"}):
        words.pop()
    return (" ".join(words).rstrip(" ,;:-|") + "…") if words else ""


def _slide_tweet_card(text: str, name: str, handle: str, p: dict,
                      rw: int = _RW, rh: int = 0, avatar: Image.Image = None) -> Image.Image:
    """Social-screenshot style, matched to the real genre: pure-black frame CROPPED TO
    CONTENT (dynamic height, like an actual screenshot), regular-weight statement text,
    author row with avatar + name + the author's title (no verified mark, no invented
    handle), subtle overflow dots top-right.
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
    line_w = rw - pad_x - tx - int(70 * _SCALE)   # up to the overflow dots
    name_txt = _fit_one_line(draw, name, f_name, line_w)
    # A LinkedIn headline runs long ("Director, X College | Business Development, Y"): the part
    # before the first "|", ended at a whole word. It used to be cut at 40 characters mid-word.
    handle = _fit_one_line(draw, re.split(r"\s+[|•·]\s+", (handle or "").strip())[0], f_handle, line_w)
    # With no title under it, the name centres on the photo instead of sitting at its top
    name_y = ay + int(2 * _SCALE) if (handle or "").strip() else ay + av_r - draw.textbbox((0, 0), name_txt or "A", font=f_name)[3] // 2
    draw.text((tx, name_y), name_txt, font=f_name, fill=(255, 255, 255))
    # No verified seal and no @handle. Both were invented: the card implied an X
    # post and a verification nobody had, and the made-up handle could belong to a
    # stranger. The second line is the author's real title, when there is one.
    draw.text((tx, ay + int(50 * _SCALE)), handle, font=f_handle, fill=(113, 118, 123))

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


# ── Designed image cards, editorial (light or dark) ───────────────────────────
# The list and quote cards were dark gradients with neon accents, glowing circles and dot grids:
# the look people now read as AI-made, picked by industry so every tech profile got the same blue.
# These share the carousel's editorial system instead: flat paper (light) or charcoal (dark),
# black-weight type, a highlighter mark on the key words, and the author's photo and name
# signing the card. Colours are the house set unless the profile has its own brand colour.
_ED_THEMES = {
    "light": {"bg": (244, 240, 232), "ink": (23, 21, 31), "body": (58, 53, 70), "muted": (122, 114, 99),
              "rule": (221, 213, 199), "accent": (36, 54, 94), "marker": (246, 214, 112), "on_marker": (23, 21, 31),
              "mark_top": 0.30},   # a highlighter stroke over the lower part of the words
    "mint":  {"bg": (231, 245, 237), "ink": (15, 29, 48), "body": (44, 62, 84), "muted": (96, 118, 136),
              "rule": (200, 225, 211), "accent": (29, 78, 216), "marker": (160, 228, 186), "on_marker": (15, 29, 48),
              "mark_top": 0.30},   # pale mint, blue numbers, light-green highlighter
    "sky":   {"bg": (234, 242, 251), "ink": (15, 23, 42), "body": (51, 65, 85), "muted": (100, 116, 139),
              "rule": (206, 221, 240), "accent": (4, 120, 87), "marker": (178, 240, 200), "on_marker": (15, 23, 42),
              "mark_top": 0.30},   # pale blue, green numbers, mint highlighter
    "sage":  {"bg": (236, 241, 232), "ink": (20, 33, 27), "body": (55, 70, 62), "muted": (110, 124, 114),
              "rule": (211, 221, 205), "accent": (30, 48, 92), "marker": (214, 240, 140), "on_marker": (20, 33, 27),
              "mark_top": 0.30},   # soft sage, deep navy numbers, lime highlighter
    "lavender": {"bg": (241, 237, 251), "ink": (26, 20, 46), "body": (62, 54, 86), "muted": (116, 108, 140),
              "rule": (221, 213, 240), "accent": (91, 33, 182), "marker": (221, 206, 255), "on_marker": (26, 20, 46),
              "mark_top": 0.30},   # lavender, violet numbers, lilac highlighter
    "peach": {"bg": (253, 239, 229), "ink": (38, 24, 18), "body": (78, 58, 48), "muted": (136, 112, 98),
              "rule": (240, 218, 202), "accent": (15, 102, 107), "marker": (255, 203, 164), "on_marker": (38, 24, 18),
              "mark_top": 0.30},   # peach, deep teal numbers, apricot highlighter
    "rose":  {"bg": (252, 237, 241), "ink": (40, 18, 27), "body": (82, 52, 63), "muted": (138, 108, 118),
              "rule": (240, 214, 223), "accent": (136, 19, 55), "marker": (251, 204, 220), "on_marker": (40, 18, 27),
              "mark_top": 0.30},   # rose, wine numbers, pink highlighter
    "sand":  {"bg": (243, 237, 224), "ink": (24, 30, 22), "body": (60, 66, 54), "muted": (120, 122, 104),
              "rule": (224, 215, 196), "accent": (22, 101, 52), "marker": (190, 232, 160), "on_marker": (24, 30, 22),
              "mark_top": 0.30},   # sand, forest numbers, leaf-green highlighter
    "forest": {"bg": (17, 43, 34), "ink": (240, 246, 240), "body": (198, 216, 206), "muted": (132, 160, 146),
              "rule": (44, 76, 63), "accent": (120, 226, 178), "marker": (120, 226, 178), "on_marker": (17, 43, 34),
              "mark_top": 0.04},   # deep forest, mint numbers and mark
    "dark":  {"bg": (22, 24, 29), "ink": (244, 241, 234), "body": (205, 207, 214), "muted": (138, 144, 156),
              "rule": (52, 56, 66), "accent": (214, 170, 72), "marker": (214, 170, 72), "on_marker": (22, 24, 29),
              "mark_top": 0.04},   # dark words on the mark: it has to cover them whole
}


def _pick_card_theme(theme: str = "") -> str:
    """The colour set asked for, else a different look from post to post: any of them, at random."""
    import random
    return theme if theme in _ED_THEMES else random.choice(list(_ED_THEMES))


def _option_themes(theme: str, n: int) -> list[str]:
    """n different colour sets for side-by-side options, the one asked for first."""
    import random
    first = _pick_card_theme(theme)
    rest = [t for t in _ED_THEMES if t != first]
    return [first] + random.sample(rest, max(0, min(n, len(_ED_THEMES)) - 1))


def _brand_accent(company: dict | None):
    """The profile's own brand colour, if it has one (a stored theme, or a brand colour)."""
    spec = _palette_from_spec((company or {}).get("theme_spec") or {})
    if spec:
        return tuple(spec["accent"])
    hx = (company or {}).get("brand_color", "")
    if hx and hx.startswith("#"):
        rgb = _hex_to_rgb(hx)
        if rgb and 0.10 < _luminance(rgb) < 0.92:
            return rgb
    return None


_KIT_COLOURS = ("bg", "ink", "body", "muted", "rule", "accent", "marker", "on_marker")
BRAND_LOOKS = ("brand", "brand-2", "brand-3")   # carousel_theme values for a brand kit's looks, in order


def _brand_looks(company: dict | None) -> list:
    """A profile's own looks for its decks, set by the team from the author's own designs:
    brand_kit = {"site": "www.example.com", "looks": [{"name": "Cream", hex colours for every
    editorial key, "mark": "text" (key words in the accent colour, no highlighter), "cover":
    "dark" (cover in the reversed colours, like the last slide)}, ...]}. A stored kit is data:
    a look with any bad colour is None, and its deck falls back to the usual colour sets."""
    kit = (company or {}).get("brand_kit")
    if not isinstance(kit, dict) or not isinstance(kit.get("looks"), list):
        return []
    site = str(kit.get("site") or "").strip()[:40]
    out = []
    for look in kit["looks"][:len(BRAND_LOOKS)]:
        t = {}
        for key in _KIT_COLOURS:
            rgb = _hex_to_rgb(str(look.get(key, ""))) if isinstance(look, dict) else None
            if not rgb:
                t = None
                break
            t[key] = rgb
        if t:
            t.update(mark_top=0.04, mark="text" if look.get("mark") == "text" else "",
                     cover="dark" if look.get("cover") == "dark" else "", site=site)
        out.append(t)
    return out


def _brand_kit_theme(company: dict | None, look: str) -> dict | None:
    """The brand kit look a carousel_theme value names ("brand", "brand-2", "brand-3"), if valid."""
    looks = _brand_looks(company)
    i = BRAND_LOOKS.index(look) if look in BRAND_LOOKS else len(looks)
    return looks[i] if i < len(looks) else None


def _ed_theme(theme: str, brand_accent: tuple | None = None) -> dict:
    t = dict(_ED_THEMES.get(theme, _ED_THEMES["light"]))
    if brand_accent:   # a real brand colour, made readable on this background
        a = tuple(brand_accent)
        if _luminance(t["bg"]) > 0.5:
            t["accent"] = a if _luminance(a) < 0.32 else _mix(a, t["ink"], 0.55)
        else:
            t["accent"] = a if _luminance(a) > 0.45 else _mix(a, (255, 255, 255), 0.45)
    return t


def _ed_line_h(draw, font) -> int:
    return draw.textbbox((0, 0), "Hg", font=font)[3]


def _phrase_marks(text: str, emphasis: str) -> set:
    """Word positions (in text.split()) of the emphasis phrase's first appearance, as a phrase."""
    norm = lambda w: w.strip('.,!?;:"\'“”‘’()').lower()
    target = [norm(w) for w in (emphasis or "").split() if norm(w)]
    flat = [norm(w) for w in (text or "").split()]
    for i in range(len(flat) - len(target) + 1) if target else []:
        if flat[i:i + len(target)] == target:
            return set(range(i, i + len(target)))
    return set()


def _ed_draw_marked(draw, text: str, font, x: int, y: int, max_w: int, t: dict, emphasis: str,
                    gap: int, color=None) -> int:
    """Left-aligned wrapped text; the emphasis phrase sits on a highlighter mark. Returns the new y.
    The phrase is marked where it first appears, as a phrase: marking each of its words anywhere
    lit up every "the" in a headline whose phrase was "the pushback"."""
    marked = _phrase_marks(text, emphasis)
    space, lh = _tw(draw, " ", font), _ed_line_h(draw, font)
    color = color or t["ink"]
    in_colour = t.get("mark") == "text"        # a brand kit's key words: accent-coloured, no mark
    wi = 0                                     # index of the line's first word in the whole text
    for line in _wrap(draw, text, font, max_w):
        words = line.split()
        widths = [_tw(draw, w, font) for w in words]
        marks = [(wi + k) in marked for k in range(len(words))]
        wi += len(words)
        cx = x
        for i, ww in enumerate(widths):       # the marks first, joined across marked neighbours
            if marks[i] and not in_colour:
                x1 = cx + ww + (space if i + 1 < len(words) and marks[i + 1] else 0)
                draw.rectangle([cx - int(8 * _SCALE), y + int(lh * t.get("mark_top", 0.30)), x1 + int(8 * _SCALE), y + int(lh * 1.06)],
                               fill=t["marker"])
            cx += ww + space
        cx = x
        for i, (w, ww) in enumerate(zip(words, widths)):
            draw.text((cx, y), w, font=font, fill=(t["accent"] if in_colour else t["on_marker"]) if marks[i] else color)
            cx += ww + space
        y += lh + gap
    return y


def _ed_block_h(draw, text: str, font, max_w: int, gap: int) -> int:
    n = len(_wrap(draw, text, font, max_w))
    return n * _ed_line_h(draw, font) + gap * max(0, n - 1)


def _ed_signature(img, draw, avatar, name: str, title: str, x: int, y: int, max_w: int, t: dict, accent):
    """The author signing the card: photo (or initial), name, title. y is the top of the row."""
    r = int(36 * _SCALE)
    if avatar is not None:
        av = avatar.convert("RGB").resize((r * 2, r * 2), Image.LANCZOS)
        m = Image.new("L", (r * 2, r * 2), 0)
        ImageDraw.Draw(m).ellipse([0, 0, r * 2, r * 2], fill=255)
        img.paste(av, (x, y), m)
    else:
        _draw_circle(draw, x + r, y + r, r, accent)
        f_i = _font(32 * _SCALE, bold=True)
        ini = (name or "V").strip()[0].upper()
        ib = draw.textbbox((0, 0), ini, font=f_i)
        draw.text((x + r - (ib[2] - ib[0]) // 2 - ib[0], y + r - (ib[3] - ib[1]) // 2 - ib[1]), ini, font=f_i, fill=t["bg"])
    tx, w = x + r * 2 + int(20 * _SCALE), max_w - r * 2 - int(20 * _SCALE)
    f_n, f_t = _font(28 * _SCALE, bold=True), _font(23 * _SCALE)
    nm = _fit_one_line(draw, name, f_n, w)
    tt = _fit_one_line(draw, re.split(r"\s+[|•·]\s+", (title or "").strip())[0], f_t, w)
    if tt:
        draw.text((tx, y + int(6 * _SCALE)), nm, font=f_n, fill=t["ink"])
        draw.text((tx, y + int(42 * _SCALE)), tt, font=f_t, fill=t["muted"])
    else:
        draw.text((tx, y + r - _ed_line_h(draw, f_n) // 2), nm, font=f_n, fill=t["ink"])


def _ed_points(points: list) -> list:
    """'1. Label: rest' -> ('Label', 'rest'); the card numbers them itself."""
    out = []
    for pt in points or []:
        s = re.sub(r"^\s*(?:\d{1,2}\s*[.):-]|[-•*→✓])\s*", "", str(pt)).strip()
        if not s:
            continue
        m = re.match(r"^([^:]{3,48}):\s+(.+)$", s)
        out.append((m.group(1).strip(), m.group(2).strip()) if m else ("", s))
    return out[:4]


def _ed_list_card(tag: str, headline: str, emphasis: str, points: list, name: str, title: str = "",
                  avatar=None, theme: str = "light", brand_accent=None,
                  rw: int = 1080 * _SCALE, rh: int = 1350 * _SCALE) -> Image.Image:
    t = _ed_theme(theme, brand_accent)
    img = Image.new("RGB", (rw, rh), t["bg"])
    draw = ImageDraw.Draw(img)
    S = _SCALE
    pad = 84 * S
    max_w = rw - pad * 2
    y = 92 * S
    tag = (tag or "").strip().upper()[:28]
    if tag:
        f_tag = _font(24 * S, bold=True)
        draw.rectangle([pad, y + 13 * S, pad + 34 * S, y + 17 * S], fill=t["accent"])
        draw.text((pad + 48 * S, y), tag, font=f_tag, fill=t["accent"])
        y += 70 * S
    sig_h = 72 * S
    sig_y = rh - pad - sig_h
    bottom = sig_y - 56 * S                       # the rule above the signature sits here
    # Headline: as big as fits in three lines
    head_gap = 10 * S
    f_head = None
    for s in (88, 80, 72, 64, 58, 52):
        f_try = _font_black(s * S)
        if len(_wrap(draw, headline, f_try, max_w)) <= 3:
            f_head = f_try
            break
    f_head = f_head or _font_black(52 * S)
    h_head = _ed_block_h(draw, headline, f_head, max_w, head_gap)
    after_head = 60 * S
    # Points: just their short labels, as large as fits; the spare room spread between them.
    # (Explanations under each point made the card a second copy of the caption.)
    pts = [lab or desc for lab, desc in _ed_points(points)]
    f_num = _font_black(64 * S)
    num_w = _tw(draw, "00", f_num) + 34 * S
    text_w = max_w - num_w
    min_gap, max_gap = 40 * S, 120 * S
    avail = bottom - y - h_head - after_head
    f_lab = None
    for s in (58, 54, 50, 46, 42, 38, 34):
        f_try = _font(s * S, bold=True)
        rows = [_ed_block_h(draw, pt, f_try, text_w, 10 * S) for pt in pts]
        if sum(rows) + min_gap * max(0, len(rows) - 1) <= avail:
            f_lab = f_try
            break
    if f_lab is None:
        f_lab = _font(34 * S, bold=True)
        rows = [_ed_block_h(draw, pt, f_lab, text_w, 10 * S) for pt in pts]
    spare = avail - sum(rows)
    gap = max(min_gap, min(max_gap, spare // max(1, len(rows) - 1))) if len(rows) > 1 else 0
    block = h_head + after_head + sum(rows) + gap * max(0, len(rows) - 1)
    y += max(0, (bottom - y - block) // 2)       # whatever is still spare: above and below, evenly
    y = _ed_draw_marked(draw, headline, f_head, pad, y, max_w, t, emphasis, head_gap)
    y += after_head - head_gap
    for i, (pt, h) in enumerate(zip(pts, rows)):
        draw.text((pad, y - 8 * S), f"{i + 1:02d}", font=f_num, fill=t["accent"])
        _ed_draw_marked(draw, pt, f_lab, pad + num_w, y, text_w, t, "", 10 * S)
        y += h
        if i < len(pts) - 1:
            draw.rectangle([pad + num_w, y + gap // 2, rw - pad, y + gap // 2 + 2 * S], fill=t["rule"])
            y += gap
    draw.rectangle([pad, sig_y - 30 * S, rw - pad, sig_y - 28 * S], fill=t["rule"])
    _ed_signature(img, draw, avatar, name, title, pad, sig_y, max_w, t, t["accent"])
    return img.resize((rw // S, rh // S), Image.LANCZOS)


def _ed_photo_card(photo, headline: str, emphasis: str, name: str, title: str = "", avatar=None,
                   theme: str = "light", brand_accent=None, rw: int = 1080 * _SCALE, rh: int = 1350 * _SCALE) -> Image.Image:
    """A relevant photo with the post's point under it: the photo carries the moment, one short line
    carries the message. The text sits on a colour panel, never on the photo, so it always reads."""
    t = _ed_theme(theme, brand_accent)
    S = _SCALE
    img = Image.new("RGB", (rw, rh), t["bg"])
    draw = ImageDraw.Draw(img)
    pad = 72 * S
    max_w = rw - pad * 2
    f_head = None
    for s in (70, 64, 58, 52, 46):
        f_try = _font_black(s * S)
        if len(_wrap(draw, headline, f_try, max_w)) <= 3:
            f_head = f_try
            break
    f_head = f_head or _font_black(46 * S)
    h_head = _ed_block_h(draw, headline, f_head, max_w, 10 * S) if (headline or "").strip() else 0
    sig_h = 72 * S
    panel_h = max(int(rh * 0.30), 52 * S + h_head + 44 * S + sig_h + pad)
    photo_h = rh - panel_h
    img.paste(_cover_crop(photo, rw, photo_h), (0, 0))
    y = photo_h + 52 * S
    if h_head:
        _ed_draw_marked(draw, headline, f_head, pad, y, max_w, t, emphasis, 10 * S)
    _ed_signature(img, draw, avatar, name, title, pad, rh - pad - sig_h, max_w, t, t["accent"])
    return img.resize((rw // S, rh // S), Image.LANCZOS)


def _ed_quote_card(headline: str, subtext: str, emphasis: str, tag: str, name: str, title: str = "",
                   avatar=None, theme: str = "light", brand_accent=None,
                   rw: int = 1080 * _SCALE, rh: int = 1350 * _SCALE) -> Image.Image:
    t = _ed_theme(theme, brand_accent)
    img = Image.new("RGB", (rw, rh), t["bg"])
    draw = ImageDraw.Draw(img)
    S = _SCALE
    pad = 84 * S
    max_w = rw - pad * 2
    y = 92 * S
    tag = (tag or "").strip().upper()[:28]
    if tag:
        f_tag = _font(24 * S, bold=True)
        draw.rectangle([pad, y + 13 * S, pad + 34 * S, y + 17 * S], fill=t["accent"])
        draw.text((pad + 48 * S, y), tag, font=f_tag, fill=t["accent"])
        y += 70 * S
    sig_y = rh - pad - 72 * S
    bottom = sig_y - 56 * S
    head_gap = 12 * S
    f_head = None
    for s in (104, 96, 88, 80, 72, 64, 58):
        f_try = _font_black(s * S)
        if len(_wrap(draw, headline, f_try, max_w)) <= 5:
            f_head = f_try
            break
    f_head = f_head or _font_black(58 * S)
    f_sub = _font(36 * S)
    h_head = _ed_block_h(draw, headline, f_head, max_w, head_gap)
    h_sub = (40 * S + _ed_block_h(draw, subtext, f_sub, max_w, 12 * S)) if (subtext or "").strip() else 0
    block = h_head + h_sub
    y += max(0, (bottom - y - block) // 2)
    y = _ed_draw_marked(draw, headline, f_head, pad, y, max_w, t, emphasis, head_gap)
    if h_sub:
        y += 40 * S - head_gap
        _ed_draw_marked(draw, subtext, f_sub, pad, y, max_w, t, "", 12 * S, color=t["body"])
    draw.rectangle([pad, sig_y - 30 * S, rw - pad, sig_y - 28 * S], fill=t["rule"])
    _ed_signature(img, draw, avatar, name, title, pad, sig_y, max_w, t, t["accent"])
    return img.resize((rw // S, rh // S), Image.LANCZOS)


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
  12 words. 2-3 short paragraphs, varied rhythm. No hashtags.

VOICE: if a voice/author profile is provided, write the way that author talks.
FACTUAL SAFETY: never invent exact stats, product version numbers, dates, or named studies —
  hedge when unsure rather than fabricate a specific a reader could falsify.
NEVER USE: "It's not X. It's Y.", "Let that sink in", game-changer, unlock, leverage, synergy.

Return ONLY JSON: {"card_tag": "...", "card_headline": "...", "card_emphasis": "...",
"card_subtext": "...", "post_text": "..."}"""


def _finalize_image_post(result: dict, company: dict = None) -> dict:
    from generator import _strip_markdown, drop_author_furniture
    headline = _clean_slide_text(result.get("card_headline", ""))
    sub = (result.get("card_subtext") or "").strip()
    emphasis = _clean_slide_text(result.get("card_emphasis", "")) if result.get("card_emphasis") else ""
    # Only keep emphasis if it's genuinely part of the headline
    if emphasis and emphasis.lower() not in headline.lower():
        emphasis = ""
    return {
        "card_tag":      drop_author_furniture(_clean_slide_text(result.get("card_tag", "")), company) if result.get("card_tag") else "",
        "card_headline": headline,
        "card_emphasis": emphasis,
        "card_subtext":  _clean_slide_text(sub) if sub else "",
        "post_text":     drop_author_furniture(_strip_markdown(result.get("post_text", "") or ""), company),
    }


# The from-text writers get the profile's context (up to ~2,600 characters with a full knowledge
# box) in front of the pasted source, which main.py has already cut to 2,000. Cutting the whole
# thing at 2,000 here left none of the source for anyone with a long knowledge box.
_FROM_TEXT_MAX = 5000


def generate_image_post_from_text(raw_text: str, company: dict = None) -> dict:
    voice_section = ""
    if company:
        from generator import _build_voice_block
        vb = _build_voice_block(company)
        if vb:
            voice_section = f"\n\nVOICE PROFILE:\n{vb[:1200]}"
    prompt = f"""Content to turn into a single branded image post:

{raw_text[:_FROM_TEXT_MAX]}{voice_section}

Build the card around the single most quotable, scroll-stopping idea in the content.

Return ONLY valid JSON:
{{"card_tag": "...", "card_headline": "...", "card_emphasis": "...", "card_subtext": "...", "post_text": "..."}}"""
    result = generate_json(prompt, system=_IMAGE_POST_SYSTEM, max_tokens=1200, temperature=0.85)
    return _finalize_image_post(result, company)


# Image posts render as vertical 4:5 (1080x1350) — the tallest ratio LinkedIn shows
# in full before cropping, which maximizes mobile feed real estate (research-backed).
IMG_POST_W, IMG_POST_H = 1080, 1350


def render_image_post_png(content: dict, company: dict, author: dict | None = None,
                          theme: str = "", meta: dict | None = None) -> bytes:
    """The quote card at 4:5: the one line, signed by its author, in the colour set asked for
    (else a random one). meta, when given, gets what was drawn: format and colour set."""
    brand = (company or {}).get("name", "Voyce")
    name, avatar = _card_author(brand, author)
    theme = _pick_card_theme(theme)
    if meta is not None:
        meta.update(format="quote_card", theme=theme)
    img = _ed_quote_card(
        content.get("card_headline", ""), content.get("card_subtext", ""), content.get("card_emphasis", ""),
        content.get("card_tag", ""), name, (company or {}).get("designation", ""), avatar,
        theme=theme, brand_accent=_brand_accent(company),
        rw=IMG_POST_W * _SCALE, rh=IMG_POST_H * _SCALE,
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── AI photo image post (research-grounded) ──────────────────────────────────
# Research (deep-research, 2026): AI human faces/hands are the #1 credibility killer and
# "AI slop" tell, and model-rendered text garbles. Until Oct 2026 that meant flat illustrations
# only; owners' own posts are photographic, so scenes are now realistic editorial photos that
# keep the guardrails: no visible faces (people from behind / over the shoulder / out of focus),
# no readable text, no AI clichés. Any text is still added by us, never by the model.

_AI_IMAGE_SYSTEM = """You design the VISUAL for a founder's LinkedIn post, plus the caption.

FORMAT — decide this FIRST. On LinkedIn a real, relevant photo beats a designed graphic, and the
image must never repeat the caption — keep any text on it SHORT:
- "photo_card": DEFAULT. A realistic PHOTOGRAPH of a real moment from the post's world (fill
  image_concept) with ONE short line under it: card_headline = the post's point in max 9 words,
  card_emphasis = 1-3 words of it. Use whenever the point can be shown as a moment — a
  conversation, a meeting, a site visit, a desk, a decision, a team at work.
- "scene": the same kind of photo with no text, when the picture alone says it.
- "list_card": ONLY when the list itself is the value (steps, rules, a framework) → a headline
  (max 9 words) + 3-4 SHORT points in card_points, each a 2-6 word label with NO explanation
  (the caption explains them).
- "quote_card": ONE sharp quotable claim and nothing to show as a photo (card_headline max 12 words).
- "tweet_card": a HOT TAKE — contrarian, a little confrontational → a social-screenshot style card
  of that line (card_headline, max ~30 words). Use for the boldest, most argument-starting claims.
A decorative metaphor is NOT a photo; for photo_card and scene, describe a real moment.

Return JSON:
image_concept: FIRST boil the post down to its ONE core message in a short phrase (e.g. "prepare for
  the buyer's pushback before the meeting", "talk to users instead of just coding"). THEN describe, in
  2-3 sentences for a PHOTOGRAPHER, a real, candid moment that shows THAT message — the kind of frame a
  business magazine (Bloomberg Businessweek, HBR, The Economic Times) would run beside the story. A
  stranger seeing the photo beside the post should think "yes, that's what this is about."
  Example — post about preparing for a tough B2B negotiation: "Over-the-shoulder shot in a small meeting
  room the evening before a big deal: a sales manager leans in across the table while a colleague, arms
  folded, plays the hard-nosed buyer. Warm desk light, a closed laptop and coffee cups, city dusk through
  the window."
  Example — post about "talk to your users, don't just build": "A founder seen from behind, sitting across
  a cafe table from a customer, notebook closed, listening; the customer slightly out of focus, mid-gesture."
  SUBJECT vs SETTING: the post's core message is the SUBJECT — what the people in the frame are doing.
  If an AUTHOR'S WORLD is given, it is only the SETTING (the kind of office, site, clients, props) and only
  when it fits. Never swap the post's subject for the author's industry: a negotiation post by an
  equipment-sales coach shows a negotiation (maybe with a dealer's yard through the window), not a
  machine standing alone.
  HARD RULES:
  - It MUST show the post's actual subject. If you can't say in one line why it fits, pick another moment.
  - PEOPLE: welcome, as real working professionals in candid moments — but seen from behind, over the
    shoulder, or turned away, with anyone facing the camera softly out of focus. Put the sharp focus on
    hands, objects or the nearest person's back. NO sharp, clearly visible faces (AI faces look fake, and
    could be mistaken for a real person), no direct eye contact with the camera. If an AUTHOR'S REGION is
    given, the people and places belong there.
  - NO readable text anywhere: the image tool writes gibberish on anything that carries writing, so no
    screens or phones showing content, no documents facing the camera, no whiteboards with writing, no
    signs, labels, logos, price tags or charts. Laptops closed or seen from behind; papers face down or blurred.
  - Keep it simple: one clear moment, a few elements, not a busy crowded frame.
  - BAN AI and stock clichés: robots, holograms, glowing brains or circuits, floating interfaces, handshakes
    to camera, chess pieces, lightbulbs, rockets, ladders, upward arrows, people pointing at screens,
    staged high-fives, grinning stock-photo models.
  - Do NOT produce decorative or unrelated scenery (landmarks, skylines as the subject, pretty landscapes)
    unless the post is literally about that.
alt_text: a short accessibility description of the image (<= 100 chars).
CAPTION RULE: the image carries the CLAIM; post_text makes the CASE. post_text must NOT open
with or repeat the card_headline/key_line — it expands the claim with a story, example, or
specifics, and may end with one genuine question. Card and caption together, never twice the same line.
post_text: the LinkedIn caption. First line max 60 characters, self-contained, open loop, no
  warm-up. 60-120 words total, plain words a 15-year-old uses daily, most sentences under 12
  words, varied rhythm. No hashtags. Plain text only, no markdown.

VOICE: if a voice/author profile is provided, write the caption the way that author talks.
FACTUAL SAFETY: never invent exact stats, product versions, dates, or named studies.
NEVER FABRICATE: no first-person stories, clients, conversations, numbers or results that are not
in the content ("Last week a CEO handed me..." when the content says no such thing). Expand only
with what the content and the author profile actually say.
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


_TZ_REGION = {"Asia/Kolkata": "India", "Asia/Calcutta": "India", "Asia/Dubai": "the UAE", "Asia/Singapore": "Singapore",
              "Europe/London": "the UK", "Australia/Sydney": "Australia", "Australia/Melbourne": "Australia",
              "America/New_York": "the United States", "America/Chicago": "the United States",
              "America/Los_Angeles": "the United States", "America/Toronto": "Canada"}


def _region_for(company: dict | None) -> str:
    """Where the author works, from their profile's time zone, so the people and places in a
    photo look like their world (an Indian sales coach's post shouldn't show only Western faces)."""
    return _TZ_REGION.get(((company or {}).get("timezone") or "").strip(), "")


def _build_ai_image_prompt(concept: str, p: dict, region: str = "") -> str:
    # Realistic editorial photography (was a flat illustration until Oct 2026: owners' own posts are
    # photographic, and a cartoon beside a B2B post read as clip art).
    return (
        f"A realistic editorial photograph for a business magazine feature. Scene: {concept}. "
        "Shot on a full-frame camera with a 35mm or 50mm lens, natural or practical light, shallow depth "
        "of field, true-to-life colour with a gentle, slightly warm grade, real textures and believable "
        "imperfections — candid and documentary, like a Bloomberg Businessweek or HBR feature photo, not a "
        "posed stock photo. One clear moment, simple uncluttered composition. "
        + (f"Set in {region}: the people, offices and places look like {region}. " if region else "")
        + "People: natural and candid. The sharp focus falls on hands, objects or the back and shoulder of "
        "the nearest person; anyone facing the camera is turned away or softly blurred by the shallow depth "
        "of field — NO sharp, clearly visible faces, nobody looking into the camera, hands relaxed and natural. "
        "Devices are plain, with NO brand logos (blank laptop lids). "
        "CRITICAL: absolutely NO text, NO words, NO letters, NO numbers, NO gibberish writing, NO labels, "
        "NO signs, NO logos, NO watermark, NO charts — screens off or turned away, papers face down or blurred. "
        "NO illustration, NO cartoon, NO 3D render, NO CGI sheen, NO robots, holograms or glowing AI imagery, "
        "NO grinning stock models. Vertical 4:5 composition."
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


_IMAGE_FORMATS = ("photo_card", "scene", "quote_card", "list_card", "tweet_card")


def generate_ai_image_post(raw_text: str, company: dict = None, force_format: str = None) -> dict:
    """LLM: pick the strongest idea → a concrete visual concept + caption for an AI photo.
    force_format pins the output to one of scene|quote_card|list_card|tweet_card when the
    user chose the format explicitly instead of letting the model pick."""
    from generator import _strip_markdown, drop_author_furniture
    voice_section = ""
    if company:
        from generator import _build_voice_block
        vb = _build_voice_block(company)
        if vb:
            voice_section = f"\n\nVOICE PROFILE:\n{vb[:1200]}"
    industry = (company or {}).get("industry", "").strip()
    world = (f"\nAUTHOR'S WORLD: {industry} — the SETTING only (place, people, props) where it fits; "
             "the post's own point is the subject.") if industry else ""
    region = _region_for(company)
    if region:
        world += f"\nAUTHOR'S REGION: {region} — the people and places in the photo belong there."
    prompt = f"""Content to turn into ONE realistic editorial photo + caption:

{raw_text[:_FROM_TEXT_MAX]}{voice_section}{world}

Choose the single strongest idea and design the visual for it (a real scene from the post's
world if the topic is physical/tangible; a concrete metaphor only if the topic is abstract).

RELEVANCE GATE: before answering, check your concept against the post's core claim. A reader
scrolling past must connect the image to the post's point INSTANTLY, with zero explanation.
If the connection needs a caption to make sense, discard the concept and pick a more literal one.

Also return "key_line": the single most quotable line from the post itself (max 12 words,
verbatim or lightly trimmed) — punchy enough to stand alone on a typographic card.
{f'''
REQUIRED FORMAT: "{force_format}" — the user chose this format explicitly. Design for exactly
this format and set "format" to this value.''' if force_format in _IMAGE_FORMATS else ""}
Return ONLY valid JSON:
{{"format": "photo_card|scene|list_card|quote_card|tweet_card", "image_concept": "... (photo_card and scene)",
"card_tag": "...", "card_headline": "...", "card_emphasis": "...", "card_subtext": "...",
"card_points": ["...", "..."], "alt_text": "...", "key_line": "...", "post_text": "..."}}"""
    result = generate_json(prompt, system=_AI_IMAGE_SYSTEM, max_tokens=1600, temperature=0.85)

    # ENFORCE the caption rule: the card carries the claim, the caption makes the case.
    # If the caption opens with the same words as the card, force one rewrite.
    def _overlap(a: str, b: str) -> float:
        wa = set(re.findall(r"[a-z']+", (a or "").lower()))
        wb = set(re.findall(r"[a-z']+", (b or "").lower()))
        return len(wa & wb) / max(1, min(len(wa), len(wb)))
    card_line = (result.get("card_headline") or result.get("key_line") or "").strip()
    first_line = next((l.strip() for l in (result.get("post_text") or "").splitlines() if l.strip()), "")
    if card_line and first_line and _overlap(card_line, first_line) > 0.6:
        fix = generate_json(
            f"""This LinkedIn caption OPENS with the same words that already appear on the attached
image card. Rewrite ONLY the caption so it opens differently and EXPANDS the claim with a
story, example, or specifics instead of repeating it. Keep the same voice, length, and rules.

CARD TEXT: {card_line}

CAPTION TO REWRITE:
{result.get("post_text", "")}

Return ONLY JSON: {{"post_text": "..."}}""",
            system=_AI_IMAGE_SYSTEM, max_tokens=900, temperature=0.8)
        if (fix.get("post_text") or "").strip():
            result["post_text"] = fix["post_text"]

    fmt = (result.get("format") or "").strip().lower()
    if force_format in _IMAGE_FORMATS:
        fmt = force_format
    if fmt not in _IMAGE_FORMATS:
        fmt = "photo_card" if (result.get("image_concept") or "").strip() else "quote_card"
    return {
        "format":        fmt,
        "image_concept": (result.get("image_concept") or "").strip(),
        "card_tag":      drop_author_furniture((result.get("card_tag") or "").strip(), company),
        "card_headline": (result.get("card_headline") or "").strip(),
        "card_emphasis": (result.get("card_emphasis") or "").strip(),
        "card_subtext":  (result.get("card_subtext") or "").strip(),
        "card_points":   [str(x).strip() for x in (result.get("card_points") or []) if str(x).strip()][:4],
        "alt_text":      (result.get("alt_text") or "").strip(),
        "key_line":      (result.get("key_line") or "").strip(),
        "post_text":     drop_author_furniture(_strip_markdown(result.get("post_text", "") or ""), company),
    }


def _card_author(brand: str, author: dict | None):
    """Name and avatar for a tweet card: the LinkedIn account it posts as when we know it,
    else the profile name and a photo kept under assets/avatars/<name-slug>.png."""
    name = ((author or {}).get("name") or "").strip() or brand
    avatar = None
    if (author or {}).get("avatar"):
        try:
            avatar = Image.open(io.BytesIO(author["avatar"]))
            avatar.load()
        except Exception:
            avatar = None
    if avatar is None:
        try:
            slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
            av_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "avatars", f"{slug}.png")
            if slug and os.path.exists(av_path):
                avatar = Image.open(av_path)
        except Exception:
            avatar = None
    return name, avatar


def render_ai_image_options(content: dict, company: dict, author: dict | None = None,
                            theme: str = "", n: int = 1) -> list[dict]:
    """The image for a post, and for the designed cards (list, quote) up to 3 colour versions of
    it to choose from. Those are drawn here, so the extra versions cost nothing; photos and tweet
    cards come one at a time. [{"png": bytes, "format": ..., "theme": ...}], the asked-for one first."""
    fmt = (content.get("format") or "scene").strip().lower()
    n = max(1, min(3, int(n or 1)))
    photo = None
    if fmt == "photo_card":   # one photo, however many colour versions of its panel
        photo = _ai_photo(content, company)
        if photo is None:
            n = 1             # it falls back to a card below
    designed = ((fmt == "list_card" and content.get("card_points"))
                or (fmt == "quote_card" and (content.get("card_headline") or content.get("key_line")))
                or (fmt == "photo_card" and photo is not None))
    out = []
    for th in (_option_themes(theme, n) if designed else [theme]):
        meta: dict = {}
        png = render_ai_image_png(content, company, author=author, theme=th, meta=meta,
                                  photo=(photo if photo is not None else False) if fmt == "photo_card" else None)
        out.append({"png": png, "format": meta.get("format", fmt), "theme": meta.get("theme", "")})
    return out


def _ai_photo(content: dict, company: dict):
    """The realistic photo for a post's concept (one retry), or None when the image model fails."""
    from llm import generate_image
    concept = (content.get("image_concept") or "").strip()
    if not concept:
        return None
    prompt = _build_ai_image_prompt(concept, _get_palette(company), _region_for(company))
    for _ in range(2):
        b = generate_image(prompt)
        if b:
            try:
                im = Image.open(io.BytesIO(b))
                im.load()
                return im
            except Exception:
                return None
    return None


def render_ai_image_png(content: dict, company: dict, author: dict | None = None,
                        theme: str = "", meta: dict | None = None, photo=None) -> bytes:
    """Generate a realistic editorial photo for the concept, composed to 4:5. Falls back to the
    branded insight card if image generation is unavailable. theme picks the designed cards'
    colour set (else random); meta, when given, gets what was drawn (format, colour set)."""
    meta = {} if meta is None else meta
    from llm import generate_image
    p = _get_palette(company)
    brand = (company or {}).get("name", "Voyce")
    concept = (content.get("image_concept") or "").strip()
    fmt = (content.get("format") or "scene").strip().lower()

    # Designed value cards: information over decoration. Rendered by us — always crisp, always relevant.
    if fmt == "list_card" and content.get("card_points"):
        name, avatar_img = _card_author(brand, author)
        theme = _pick_card_theme(theme)
        meta.update(format="list_card", theme=theme)
        img = _ed_list_card(
            content.get("card_tag", ""), content.get("card_headline") or content.get("key_line", ""),
            content.get("card_emphasis", ""), content.get("card_points"), name,
            (company or {}).get("designation", ""), avatar_img,
            theme=theme, brand_accent=_brand_accent(company),
            rw=IMG_POST_W * _SCALE, rh=IMG_POST_H * _SCALE)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    if fmt == "tweet_card" and (content.get("card_headline") or content.get("key_line")):
        # A tweet card is a hook, not a paragraph: the short line wins so the caption
        # keeps the payoff (the "see more" click is the dwell signal that drives reach).
        card_line = (content.get("card_headline") or content.get("key_line", "")).strip()
        key_line = (content.get("key_line") or "").strip()
        if key_line and len(card_line.split()) > 16 and len(key_line.split()) < len(card_line.split()):
            card_line = key_line
        content = {**content, "card_headline": card_line}
        handle = ((company or {}).get("designation") or "").strip()   # real title, never an invented @handle
        name, avatar_img = _card_author(brand, author)
        img = _slide_tweet_card(
            content.get("card_headline") or content.get("key_line", ""), name, handle, p,
            rw=IMG_POST_W * _SCALE, avatar=avatar_img)
        meta.update(format="tweet_card", theme="")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    if fmt == "photo_card":
        # photo: the one already made, False when it was tried and failed, None to make it here
        photo = _ai_photo(content, company) if photo is None else photo
        if photo is not False and photo is not None:
            name, avatar_img = _card_author(brand, author)
            theme = _pick_card_theme(theme)
            meta.update(format="photo_card", theme=theme)
            img = _ed_photo_card(photo, content.get("card_headline") or content.get("key_line", ""),
                                 content.get("card_emphasis", ""), name, (company or {}).get("designation", ""),
                                 avatar_img, theme=theme, brand_accent=_brand_accent(company),
                                 rw=IMG_POST_W * _SCALE, rh=IMG_POST_H * _SCALE)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        concept = ""   # the photo didn't come: don't ask the image model again, use the card below
    if fmt == "quote_card" and (content.get("card_headline") or content.get("key_line")):
        card = {
            "card_headline": content.get("card_headline") or content.get("key_line", ""),
            "card_subtext": content.get("card_subtext", ""),
            "card_emphasis": content.get("card_emphasis", ""),
            "card_tag": content.get("card_tag", ""),
        }
        return render_image_post_png(card, company, author=author, theme=theme, meta=meta)

    # Transient API failures are common enough to deserve one retry before falling back.
    img_bytes = None
    if concept:
        rendered_prompt = _build_ai_image_prompt(concept, p, _region_for(company))
        for _ in range(2):
            img_bytes = generate_image(rendered_prompt)
            if img_bytes:
                break
    if img_bytes:
        try:
            canvas = _cover_crop(Image.open(io.BytesIO(img_bytes)), IMG_POST_W, IMG_POST_H)
            meta.update(format="scene", theme="")
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
    return render_image_post_png(card, company, author=author, theme=theme, meta=meta)


def generate_caption_from_text(raw_text: str, company: dict = None) -> dict:
    """Caption + alt text for a user-uploaded image (no image generated)."""
    data = generate_ai_image_post(raw_text, company)
    return {"post_text": data.get("post_text", ""), "alt_text": data.get("alt_text", "")}


def _capture_page_screenshot(url: str) -> Image.Image | None:
    """Full-viewport capture of the article page via microlink (keyless free tier).
    Returns a PIL image or None on any failure — never raises."""
    import httpx
    try:
        r = httpx.get("https://api.microlink.io",
                      params={"url": url, "screenshot": True, "meta": False}, timeout=25)
        if r.status_code != 200:
            return None
        shot = ((r.json().get("data") or {}).get("screenshot") or {}).get("url")
        if not shot:
            return None
        img = httpx.get(shot, timeout=20, follow_redirects=True)
        if img.status_code == 200 and (img.headers.get("content-type") or "").startswith("image"):
            return Image.open(io.BytesIO(img.content)).convert("RGB")
    except Exception:
        return None
    return None


def capture_source_receipt(url: str, post_text: str = "") -> bytes | None:
    """Real-page citation receipt: capture the article -> Gemini-vision picks the most
    relevant region (a chart the post cites, else the headline block) -> fixed top-crop
    when vision is unavailable. None when the page couldn't be captured at all
    (callers fall back to the rendered citation card)."""
    page = _capture_page_screenshot(url)
    if page is None:
        return None
    w, h = page.size

    crop = None
    try:
        from llm import pick_image_region
        buf = io.BytesIO()
        page.save(buf, format="PNG")
        box = pick_image_region(buf.getvalue(), post_text)
        if box:
            l, r_ = int(box["left"] * w), int(box["right"] * w)
            t, b = int(box["top"] * h), int(box["bottom"] * h)
            # Sanity: wide enough to read, tall enough to mean something, short enough
            # to stay a receipt rather than a page dump.
            if (r_ - l) >= w * 0.4 and h * 0.10 <= (b - t) <= h * 0.65:
                crop = page.crop((l, t, r_, b))
    except Exception:
        crop = None

    if crop is None:
        # Headline-zone default: masthead + headline + byline live in the top ~37%.
        crop = page.crop((0, 0, w, int(h * 0.375)))

    buf = io.BytesIO()
    crop.save(buf, format="PNG")
    return buf.getvalue()


def render_source_card_png(meta: dict, company: dict = None) -> bytes:
    """Citation receipt for the article a post reacts to — clipping style: black masthead
    with the publication name, headline on paper, byline/date. Replaces hand-cropped
    screenshots of the source and puts per-post provenance on the post itself.
    meta: {publication, headline, author, date, domain}"""
    rw = IMG_POST_W * _SCALE
    pad_x = int(84 * _SCALE)
    inner = rw - pad_x * 2
    scratch = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    pub      = (meta.get("publication") or meta.get("domain") or "Source").strip()[:42]
    headline = (meta.get("headline") or "").strip()[:220] or "Untitled article"
    author   = (meta.get("author") or "").strip()[:60]
    date_s   = (meta.get("date") or "").strip()[:24]
    domain   = (meta.get("domain") or "").strip()[:60]

    # Headline: the biggest size that fits in ≤6 lines
    f_head, head_lines = None, []
    for s in (66, 60, 54, 48, 44):
        f_try = _font(s * _SCALE, bold=True)
        lines = _wrap(scratch, headline, f_try, inner)
        if len(lines) <= 6:
            f_head, head_lines = f_try, lines
            break
    if f_head is None:
        f_head = _font(44 * _SCALE, bold=True)
        head_lines = _wrap(scratch, headline, f_head, inner)[:6]
    head_gap = int(14 * _SCALE)
    h_head = _wrap_height(scratch, head_lines, f_head, head_gap)

    mast_h   = int(120 * _SCALE)
    gap_top  = int(72 * _SCALE)
    kick_h   = int(30 * _SCALE)
    gap_kick = int(44 * _SCALE)
    gap_by   = int(58 * _SCALE)
    by_h     = int(36 * _SCALE)
    bottom   = int(84 * _SCALE)
    rh = mast_h + gap_top + kick_h + gap_kick + h_head + gap_by + by_h + bottom

    paper = (250, 247, 240)
    ink   = (24, 21, 16)
    gray  = (122, 115, 102)
    img  = Image.new("RGB", (rw, rh), paper)
    draw = ImageDraw.Draw(img)

    # Masthead: black bar, publication name centered in white
    draw.rectangle([0, 0, rw, mast_h], fill=(10, 10, 10))
    f_pub = _font(46 * _SCALE, bold=True)
    pw = _tw(draw, pub, f_pub)
    ph = draw.textbbox((0, 0), pub, font=f_pub)[3]
    draw.text(((rw - pw) // 2, (mast_h - ph) // 2 - int(4 * _SCALE)), pub,
              font=f_pub, fill=(255, 255, 255))

    y = mast_h + gap_top
    f_kick = _font(26 * _SCALE, bold=True)
    kick = "REPORTED" + (f"  ·  {date_s}" if date_s else "")
    draw.text((pad_x, y), kick, font=f_kick, fill=gray)
    y += kick_h + gap_kick
    for ln in head_lines:
        draw.text((pad_x, y), ln, font=f_head, fill=ink)
        y += draw.textbbox((0, 0), ln, font=f_head)[3] + head_gap
    y += gap_by - head_gap
    f_by = _font(28 * _SCALE)
    if author:
        draw.text((pad_x, y), "By " + author, font=f_by, fill=gray)
    if domain:
        dw = _tw(draw, domain, f_by)
        draw.text((rw - pad_x - dw, y), domain, font=f_by, fill=gray)

    img = img.resize((IMG_POST_W, rh // _SCALE), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


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
- Data authority:     "New data: X. Here's what wins." — ONLY with a real, named source in the
  deck; NEVER first-person research claims ("I analyzed", "I studied", "my data") unless the
  provided content proves the author actually did it.
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
grew", "I analyzed") unless that exact claim appears in the provided content or author profile.
Attribute third-party results to their source ("Razorpay's report found...", "Founders
interviewed said..."). Any stat anywhere in the deck must name its source on the same slide or
be cut — an unverifiable number is worse than no number.

OUTPUT HYGIENE (strict):
- No markdown (**, ##, backticks), no placeholder tokens like [X] or [Company], no numbered
  prefixes in titles.
- Banned: game-changer, landscape, unlock, dive deep, revolutionize, leverage, synergy,
  in today's world, the future is here, are you ready, I'm excited, at the end of the day,
  paradigm shift, move the needle, let that sink in.

post_text: the LinkedIn caption. Hook first (max 60 characters, self-contained, open loop),
no warm-up. 60-120 words total, plain words a 15-year-old uses daily, most sentences under
12 words, varied rhythm. No hashtags.
Caption layout: the hook alone on the first line, then 2-3 short paragraphs of 2-3 sentences
each (a blank line between paragraphs), then one closing line. Never put every sentence on its
own line: a caption of one-line sentences reads as a stack of fragments, not a post.

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


def _sanitize_carousel_result(result: dict, company: dict = None) -> dict:
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
        from generator import _strip_markdown, drop_author_furniture
        result["post_text"] = _caption_paragraphs(drop_author_furniture(_strip_markdown(result.get("post_text") or ""), company))
    return result


def _caption_paragraphs(text: str) -> str:
    """Carousel captions came back one sentence per line, so on LinkedIn they read as a column of
    short fragments down the left. The hook and the closing line keep their own lines; the
    one-sentence lines between are joined into paragraphs of two or three. Lists (1., -, →, •)
    and paragraphs that are already full are left as they are."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()]
    if len(paras) < 5:
        return (text or "").strip()
    listy = lambda p: "\n" in p or bool(re.match(r"^(\d+[.)]\s|[-•→*✓✅▪]\s?)", p))
    out, buf = [paras[0]], []
    def flush():
        if buf:
            out.append(" ".join(buf))
            buf.clear()
    for p in paras[1:-1]:
        if listy(p) or len(p) > 150:
            flush()
            out.append(p)
            continue
        buf.append(p)
        if len(buf) == 3 or sum(len(x) for x in buf) >= 100:
            flush()
    if len(buf) == 1 and len(out) > 1 and not listy(out[-1]) and out[-1] != paras[0]:
        out[-1] += " " + buf.pop()   # no orphan line: it joins the paragraph before it
    flush()
    out.append(paras[-1])
    return "\n\n".join(out)


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

{raw_text[:_FROM_TEXT_MAX]}{voice_section}

Build the carousel around the single most surprising or useful insight in the content.

{_SLIDE_CONSTRAINTS}

Return ONLY valid JSON in this shape:
{_CAROUSEL_SCHEMA}"""

    result = generate_json(prompt, system=_CAROUSEL_SYSTEM, max_tokens=6000, temperature=0.85)
    return _sanitize_carousel_result(result, company)


# ── Autonomous content generation ────────────────────────────────────────────

def generate_carousel_content(company: dict, news_context: str, post_type: str) -> dict:
    from autonomous import _build_company_brief, POST_TYPE_LABELS, _hook_guidance

    is_personal = company.get("profile_type") == "personal"
    voice_note = ("First person (I, my, I've) — write as if the author is speaking directly."
                  if is_personal else "Third person — company voice, authoritative.")
    company_brief = _build_company_brief(company, with_knowledge=False)[:1400]
    # Their own facts and rules get their own place: inside the brief they came after the voice
    # examples, so the 1,400-character cut above always dropped them.
    knowledge = (company.get("knowledge") or "").strip()[:2000]
    rules = (f"\nThe author's own facts and rules (follow every one that fits a carousel; never "
             f"contradict them or invent around them):\n{knowledge}\n") if knowledge else ""
    # The brief's own note on the example posts sits after them, past the cut: decks kept
    # lifting a coined term from one example ("SELLFIE") into slide after slide.
    rules += ("\nThe example posts above show the voice only: never reuse their hooks, phrases, "
              "coined terms or stories.\n") if company.get("linkedin_top_posts") else ""
    from autonomous import _openings_to_avoid, _avoid_block
    avoid = _avoid_block(_openings_to_avoid(company))

    prompt = f"""Generate a LinkedIn carousel for {company['name']} ({company['industry']}).

Post type: {POST_TYPE_LABELS.get(post_type, post_type)} — shape the narrative arc and slide labels to fit this type.
Latest industry context: {news_context or 'Draw from well-known industry examples and published reports.'}
Author/company context: {company_brief}
{rules}
{_SLIDE_CONSTRAINTS}
Content-slide bodies must cite real companies, reports, or numbers — nothing that could apply to any industry.

{voice_note}
{_hook_guidance(company.get("allowed_hooks"))}
{avoid}
The cover slide's title and the caption's first line count as openings.

Return ONLY valid JSON in this shape:
{_CAROUSEL_SCHEMA}"""

    result = generate_json(prompt, system=_CAROUSEL_SYSTEM, max_tokens=6000, temperature=0.85)
    return _sanitize_carousel_result(result, company)


# ── PDF assembly ──────────────────────────────────────────────────────────────

# ── Editorial carousel (same system as the image cards) ───────────────────────
# The old decks were one template in nine accent colours: a dark gradient cover with a neon
# highlight, a loud colour-block stat slide, and point slides whose text sat in the top third.
# These are flat, one colour set per deck, with type sized to fill each slide, the highlighter
# on the hook's key words, and the author's photo and name on the first and last slides.

def _ed_contrast(t: dict) -> dict:
    """The deck's loud slides (a number, the last slide): a light set turns to its ink,
    a dark set to its mark colour. Same family, reversed."""
    if _luminance(t["bg"]) > 0.5:
        bg = t["ink"]
        return {**t, "bg": bg, "ink": t["bg"], "body": _mix(bg, t["bg"], 0.80), "muted": _mix(bg, t["bg"], 0.58),
                "rule": _mix(bg, t["bg"], 0.20), "accent": t["marker"], "on_marker": t["ink"], "mark_top": 0.04}
    bg, ink = t["marker"], t["bg"]
    return {**t, "bg": bg, "ink": ink, "body": _mix(bg, ink, 0.82), "muted": _mix(bg, ink, 0.62),
            "rule": _mix(bg, ink, 0.25), "accent": ink, "marker": ink, "on_marker": bg, "mark_top": 0.04}


def _ed_fit(draw, text: str, max_w: int, max_h: int, sizes, gap: int, max_lines: int, make) -> tuple:
    """The largest font from sizes whose wrapped text fits; (font, height)."""
    for s in sizes:
        f = make(s * _SCALE)
        n = len(_wrap(draw, text, f, max_w))
        h = _ed_block_h(draw, text, f, max_w, gap)
        if n <= max_lines and h <= max_h:
            return f, h
    f = make(sizes[-1] * _SCALE)
    return f, _ed_block_h(draw, text, f, max_w, gap)


def _ed_mini_avatar(img, draw, avatar, name: str, x: int, y: int, r: int, t: dict):
    if avatar is not None:
        av = avatar.convert("RGB").resize((r * 2, r * 2), Image.LANCZOS)
        m = Image.new("L", (r * 2, r * 2), 0)
        ImageDraw.Draw(m).ellipse([0, 0, r * 2, r * 2], fill=255)
        img.paste(av, (x, y), m)
        return
    _draw_circle(draw, x + r, y + r, r, t["accent"])
    f = _font(int(r * 0.95), bold=True)
    ini = (name or "V").strip()[0].upper()
    b = draw.textbbox((0, 0), ini, font=f)
    draw.text((x + r - (b[2] - b[0]) // 2 - b[0], y + r - (b[3] - b[1]) // 2 - b[1]), ini, font=f, fill=t["bg"])


def _edc_footer(img, draw, t: dict, num: int, total: int, name: str, avatar):
    """Author and progress on every inside slide: who wrote it, and how far through you are."""
    S, m = _SCALE, 84 * _SCALE
    ty = _RH - 70 * S
    draw.rectangle([m, ty, _RW - m, ty + 4 * S], fill=t["rule"])
    draw.rectangle([m, ty, m + int((_RW - 2 * m) * num / max(1, total)), ty + 4 * S], fill=t["accent"])
    r, ry = 20 * S, ty - 66 * S
    _ed_mini_avatar(img, draw, avatar, name, m, ry, r, t)
    f = _font(24 * S, bold=True)
    nx, ny = m + r * 2 + 14 * S, ry + r - _ed_line_h(draw, f) // 2
    nm = _fit_one_line(draw, name, f, _RW // 2)
    draw.text((nx, ny), nm, font=f, fill=t["ink"])
    if t.get("site"):          # a brand kit's website, after the name
        fs = _font(24 * S)
        draw.text((nx + _tw(draw, nm, f) + 9 * S, ny), _fit_one_line(draw, "·  " + t["site"], fs, _RW // 3), font=fs, fill=t["muted"])
    ctr = f"{num} / {total}"
    fc = _font(24 * S, bold=True)
    draw.text((_RW - m - _tw(draw, ctr, fc), ry + r - _ed_line_h(draw, fc) // 2), ctr, font=fc, fill=t["muted"])
    return ry - 40 * S          # the lowest y the slide's content may use


def _edc_eyebrow(draw, text: str, x: int, y: int, t: dict) -> int:
    S = _SCALE
    f = _font(24 * S, bold=True)
    draw.rectangle([x, y + 13 * S, x + 34 * S, y + 17 * S], fill=t["accent"])
    draw.text((x + 48 * S, y), text.upper()[:40], font=f, fill=t["accent"])
    return y + 64 * S


def _edc_cover(headline: str, subtext: str, total: int, t: dict, name: str, title: str, avatar) -> Image.Image:
    S, m = _SCALE, 84 * _SCALE
    img = Image.new("RGB", (_RW, _RH), t["bg"]); draw = ImageDraw.Draw(img)
    max_w = _RW - m * 2
    top = _edc_eyebrow(draw, f"Swipe through · {total} slides", m, 100 * S, t)
    base, kicker = _split_hook_pill(headline)
    full = (base + " " + kicker).strip() if kicker else headline
    sig_y = _RH - m - 72 * S
    bottom = sig_y - 70 * S
    f_head, h_head = _ed_fit(draw, full, max_w, int((bottom - top) * 0.74), (136, 124, 112, 100, 90, 80, 72), 12 * S, 6, _font_black)
    sub = (subtext or "").strip()
    f_sub, h_sub = _ed_fit(draw, sub, max_w, 200 * S, (42, 38, 34), 12 * S, 3, _font) if sub else (None, 0)
    block = h_head + (48 * S + h_sub if sub else 0)
    y = top + max(0, (bottom - top - block) * 2 // 5)
    y = _ed_draw_marked(draw, full, f_head, m, y, max_w, t, kicker, 12 * S)
    if sub:
        _ed_draw_marked(draw, sub, f_sub, m, y + 48 * S - 12 * S, max_w, t, "", 12 * S, color=t["body"])
    _ed_signature(img, draw, avatar, name, title, m, sig_y, max_w - 150 * S, t, t["accent"])
    r = 44 * S                   # swipe arrow
    cx, cy = _RW - m - r, sig_y + 36 * S
    _draw_circle(draw, cx, cy, r, t["accent"])
    fa = _font(44 * S, bold=True)
    b = draw.textbbox((0, 0), "→", font=fa)
    draw.text((cx - (b[2] - b[0]) // 2 - b[0], cy - (b[3] - b[1]) // 2 - b[1]), "→", font=fa, fill=t["bg"])
    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


def _edc_point(title: str, body: str, num: int, total: int, t: dict, name: str, avatar,
               numeral: str = "", eyebrow: str = "") -> Image.Image:
    S, m = _SCALE, 84 * _SCALE
    img = Image.new("RGB", (_RW, _RH), t["bg"]); draw = ImageDraw.Draw(img)
    max_w = _RW - m * 2
    bottom = _edc_footer(img, draw, t, num, total, name, avatar)
    top = 110 * S
    if eyebrow:
        top = _edc_eyebrow(draw, eyebrow, m, top, t)
    f_num = _font_black(250 * S)
    h_num = (_ed_line_h(draw, f_num) + 24 * S) if numeral else 0
    avail = bottom - top - h_num
    f_t, h_t = _ed_fit(draw, title, max_w, int(avail * 0.48), (112, 104, 96, 88, 80, 72, 64, 56), 14 * S, 3, _font_black)
    body = (body or "").strip()
    gap_tb = 52 * S + 10 * S + 52 * S
    f_b, h_b = _ed_fit(draw, body, max_w, max(0, avail - h_t - gap_tb), (56, 52, 48, 44, 40, 36, 32), 16 * S, 7, _font) if body else (None, 0)
    block = h_num + h_t + (gap_tb + h_b if body else 0)
    y = top + max(0, (bottom - top - block) // 2)
    if numeral:
        draw.text((m - 6 * S, y - 30 * S), numeral, font=f_num, fill=t["accent"])
        y += h_num
    y = _ed_draw_marked(draw, title, f_t, m, y, max_w, t, "", 14 * S)
    if body:
        y += 52 * S - 14 * S
        draw.rectangle([m, y, m + 110 * S, y + 12 * S], fill=t["marker"])
        y += 10 * S + 52 * S
        _ed_draw_marked(draw, body, f_b, m, y, max_w, t, "", 16 * S, color=t["body"])
    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


def _edc_stat(stat: str, title: str, body: str, num: int, total: int, t: dict, name: str, avatar) -> Image.Image:
    S, m = _SCALE, 84 * _SCALE
    tc = _ed_contrast(t)
    img = Image.new("RGB", (_RW, _RH), tc["bg"]); draw = ImageDraw.Draw(img)
    max_w = _RW - m * 2
    bottom = _edc_footer(img, draw, tc, num, total, name, avatar)
    top = 110 * S
    f_s = None
    for s in (330, 290, 250, 210, 180, 150, 120):
        f_try = _font_black(s * S)
        if _tw(draw, stat, f_try) <= max_w:
            f_s = f_try
            break
    f_s = f_s or _font_black(120 * S)
    h_s = _ed_line_h(draw, f_s)
    f_t, h_t = _ed_fit(draw, title, max_w, 320 * S, (76, 70, 64, 58, 52), 12 * S, 3, lambda z: _font(z, bold=True))
    body = (body or "").strip()
    f_b, h_b = _ed_fit(draw, body, max_w, 380 * S, (48, 44, 40, 36, 32), 14 * S, 5, _font) if body else (None, 0)
    block = h_s + 30 * S + h_t + (36 * S + h_b if body else 0)
    y = top + max(0, (bottom - top - block) // 2)
    draw.text((m - 8 * S, y - int(h_s * 0.12)), stat, font=f_s, fill=tc["accent"])
    y += h_s + 30 * S
    y = _ed_draw_marked(draw, title, f_t, m, y, max_w, tc, "", 10 * S)
    if body:
        _ed_draw_marked(draw, body, f_b, m, y + 36 * S - 10 * S, max_w, tc, "", 12 * S, color=tc["body"])
    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


def _edc_recap(title: str, bullets: list, num: int, total: int, t: dict, name: str, avatar) -> Image.Image:
    S, m = _SCALE, 84 * _SCALE
    img = Image.new("RGB", (_RW, _RH), t["bg"]); draw = ImageDraw.Draw(img)
    max_w = _RW - m * 2
    bottom = _edc_footer(img, draw, t, num, total, name, avatar)
    top = _edc_eyebrow(draw, "Recap", m, 110 * S, t)
    items = [str(b).strip() for b in (bullets or []) if str(b).strip()][:5]
    f_t, h_t = _ed_fit(draw, title, max_w, 320 * S, (104, 96, 88, 80, 72, 64), 12 * S, 2, _font_black)
    ck = 30 * S
    tx = m + ck * 2 + 28 * S
    text_w = _RW - m - tx
    avail = bottom - top - h_t - 70 * S
    f_i = None
    for s in (58, 54, 50, 46, 42, 38, 34, 31):
        f_try = _font(s * S, bold=True)
        rows = [_ed_block_h(draw, it, f_try, text_w, 8 * S) for it in items]
        if sum(rows) + 40 * S * max(0, len(rows) - 1) <= avail:
            f_i = f_try
            break
    f_i = f_i or _font(31 * S, bold=True)
    rows = [_ed_block_h(draw, it, f_i, text_w, 8 * S) for it in items]
    gap = max(40 * S, min(120 * S, (avail - sum(rows)) // max(1, len(rows))))
    block = h_t + 70 * S + sum(rows) + gap * max(0, len(rows) - 1)
    y = top + max(0, (bottom - top - block) * 2 // 5)
    y = _ed_draw_marked(draw, title, f_t, m, y, max_w, t, "", 12 * S) + 70 * S - 12 * S
    for it, h in zip(items, rows):
        cy = y + _ed_line_h(draw, f_i) // 2
        _draw_circle(draw, m + ck, cy, ck, t["marker"])
        w = max(4, int(6 * S))
        draw.line([(m + ck - 11 * S, cy), (m + ck - 3 * S, cy + 9 * S), (m + ck + 12 * S, cy - 9 * S)], fill=t["on_marker"], width=w)
        _ed_draw_marked(draw, it, f_i, tx, y, text_w, t, "", 8 * S)
        y += h + gap
    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


def _edc_last(headline: str, cta: str, total: int, t: dict, name: str, title: str, avatar,
              topics: list | None = None) -> Image.Image:
    """The last slide: the takeaway and the one ask on top, and an author card below it (a large
    photo, name, title and what they post about), the reason to follow, the way creators end a deck."""
    S, m = _SCALE, 84 * _SCALE
    tc = _ed_contrast(t)
    img = Image.new("RGB", (_RW, _RH), tc["bg"]); draw = ImageDraw.Draw(img)
    max_w = _RW - m * 2
    topics = [x for x in (topics or []) if x][:3]
    # the author card
    card_h = (460 if topics else 300) * S
    cy0 = _RH - m - card_h
    _draw_rounded_rect(draw, m, cy0, _RW - m, _RH - m, 36 * S, _mix(tc["bg"], tc["ink"], 0.09))
    r = 86 * S
    ax, ay = m + 48 * S, cy0 + 48 * S
    _ed_mini_avatar(img, draw, avatar, name, ax, ay, r, tc)
    tx = ax + r * 2 + 36 * S
    tw = _RW - m - 48 * S - tx
    f_n = _font_black(54 * S)
    f_ti = _font(28 * S)
    nm = _fit_one_line(draw, name, f_n, tw)
    t_lines = _wrap(draw, re.split(r"\s+[|•·]\s+", (title or "").strip())[0], f_ti, tw)
    if len(t_lines) > 2:
        t_lines = [t_lines[0], _fit_one_line(draw, " ".join(t_lines[1:]), f_ti, tw)]
    h_txt = _ed_line_h(draw, f_n) + (14 * S + len(t_lines) * (_ed_line_h(draw, f_ti) + 8 * S) if t_lines else 0)
    ny = ay + r - h_txt // 2
    draw.text((tx, ny), nm, font=f_n, fill=tc["ink"])
    ny += _ed_line_h(draw, f_n) + 14 * S
    for ln in t_lines:
        draw.text((tx, ny), ln, font=f_ti, fill=tc["muted"])
        ny += _ed_line_h(draw, f_ti) + 8 * S
    if topics:
        ry = ay + r * 2 + 40 * S
        draw.rectangle([ax, ry, _RW - m - 48 * S, ry + 2 * S], fill=tc["rule"])
        f_l = _font(22 * S, bold=True)
        draw.text((ax, ry + 30 * S), "POSTS ABOUT", font=f_l, fill=tc["accent"])
        f_tp = _font(32 * S, bold=True)
        tp_w = _RW - m - 48 * S - ax
        lines = _wrap(draw, " · ".join(topics), f_tp, tp_w)
        if len(lines) > 2:
            lines = [lines[0], _fit_one_line(draw, " ".join(lines[1:]), f_tp, tp_w)]
        ty = ry + 68 * S
        for ln in lines:
            draw.text((ax, ty), ln, font=f_tp, fill=tc["ink"])
            ty += _ed_line_h(draw, f_tp) + 8 * S
    # the takeaway and the ask
    top = _edc_eyebrow(draw, "The takeaway", m, 110 * S, tc)
    bottom = cy0 - 70 * S
    base, kicker = _split_hook_pill(headline)
    full = (base + " " + kicker).strip() if kicker else headline
    f_h, h_h = _ed_fit(draw, full, max_w, int((bottom - top) * 0.74), (112, 104, 94, 84, 76, 68, 60), 12 * S, 5, _font_black)
    cta = (cta or "").strip()
    f_c = next((f for f in (_font(z * S, bold=True) for z in (34, 31, 28, 25)) if _tw(draw, cta, f) <= max_w - 64 * S),
               _font(25 * S, bold=True))   # the whole ask if it fits at any of these sizes
    cta_txt = _fit_one_line(draw, cta, f_c, max_w - 64 * S) if cta else ""
    h_c = (_ed_line_h(draw, f_c) + 44 * S) if cta_txt else 0
    block = h_h + (56 * S + h_c if cta_txt else 0)
    y = top + max(0, (bottom - top - block) // 2)
    y = _ed_draw_marked(draw, full, f_h, m, y, max_w, tc, kicker, 12 * S)
    if cta_txt:
        y += 56 * S - 12 * S
        w = _tw(draw, cta_txt, f_c) + 64 * S
        _draw_rounded_rect(draw, m, y, m + w, y + h_c, h_c // 2, tc["marker"])
        draw.text((m + 32 * S, y + 22 * S), cta_txt, font=f_c, fill=tc["on_marker"])
    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


_OLD_CAROUSEL_THEMES = {"warm_violet": "light", "clean_light": "lavender", "warm_paper": "peach",
                        "deep_teal": "forest", "warm_dark": "dark", "dark_pro": "dark",
                        "fintech_navy": "dark", "deep_indigo": "lavender", "electric": "forest"}


def _carousel_theme_for(company: dict | None, theme: str = "") -> str:
    """The colour set for a deck: one asked for, the profile's own (older theme names map to the
    nearest new set), or a different one each deck."""
    for want in (theme, (company or {}).get("carousel_theme", "")):
        want = _OLD_CAROUSEL_THEMES.get(want, want)
        if want in _ED_THEMES:
            return want
    return _pick_card_theme("")


def render_carousel_slides(content: dict, company: dict, author: dict | None = None, theme: str = "") -> list:
    """The deck as slide images, in the editorial design."""
    hook, context = content["hook_slide"], content.get("context_slide")
    c_slides, recap, cta = content.get("content_slides", []), content.get("recap_slide"), content["cta_slide"]
    total = 1 + (1 if context else 0) + len(c_slides) + (1 if recap else 0) + 1
    # A brand kit look, when one is asked for here or picked as the profile's carousel look
    kit = _brand_kit_theme(company, theme or (company or {}).get("carousel_theme", ""))
    t = kit or _ed_theme(_carousel_theme_for(company, theme), _brand_accent(company))
    name, avatar = _card_author((company or {}).get("name", "") or "Voyce", author)
    title = (company or {}).get("designation", "")
    t_cover = _ed_contrast(t) if t.get("cover") == "dark" else t
    slides = [_edc_cover(hook.get("headline", ""), hook.get("subtext", ""), total, t_cover, name, title, avatar)]
    num = 2
    if context:
        slides.append(_edc_point(context.get("title", ""), context.get("body", ""), num, total, t, name, avatar, eyebrow="Start here"))
        num += 1
    step = 0
    for s in c_slides:
        if s.get("kind") == "stat" and s.get("stat"):
            slides.append(_edc_stat(s["stat"], s.get("title", ""), s.get("body", ""), num, total, t, name, avatar))
        else:
            step += 1
            slides.append(_edc_point(s.get("title", ""), s.get("body", ""), num, total, t, name, avatar, numeral=f"{step:02d}"))
        num += 1
    if recap:
        slides.append(_edc_recap(recap.get("title", ""), recap.get("bullets", []), num, total, t, name, avatar))
        num += 1
    # all their topics (a daily run narrows "industry" to today's one and keeps the full list here)
    all_t = (company or {}).get("all_topics") or (company or {}).get("industry", "") or ""
    topics = [x.strip(" .-") for x in re.split(r"[,;\n|]+", all_t) if len(x.strip()) >= 3]
    slides.append(_edc_last(cta.get("headline", ""), cta.get("cta", ""), total, t, name, title, avatar, topics))
    return slides


def slide_previews(images: list, width: int = 540) -> list[str]:
    """Base64 JPEGs, small enough to show in the queue and the approval email page,
    so a carousel is never approved without seeing its slides."""
    import base64
    out = []
    for im in images:
        try:
            t = im.convert("RGB")
            t.thumbnail((width, int(width * 1.25)))
            b = io.BytesIO()
            t.save(b, format="JPEG", quality=72, optimize=True)
            out.append(base64.b64encode(b.getvalue()).decode())
        except Exception:
            logging.exception("[Carousel] preview failed")
    return out


def render_carousel_pdf(content: dict, company: dict, previews: list | None = None,
                        author: dict | None = None, theme: str = "") -> bytes:
    """The deck as a PDF, in the editorial design (render_carousel_slides). author: the LinkedIn
    name and photo it posts as, for the cover and the last slide; theme: a colour set, else the
    profile's own, else a different one each deck."""
    slides = render_carousel_slides(content, company, author=author, theme=theme)

    buf = io.BytesIO()
    if previews is not None:
        previews.extend(slide_previews(slides))
    slides[0].save(buf, format="PDF", save_all=True, append_images=slides[1:], resolution=300)
    return buf.getvalue()
