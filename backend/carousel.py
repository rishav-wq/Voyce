import base64
import colorsys
import io
import json
import logging
import os
import re

from PIL import Image, ImageDraw, ImageFont
from dotenv import load_dotenv

from llm import generate_json, CLAUDE_CAROUSEL_MODEL, CLAUDE_CAROUSEL_EFFORT

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
    words, lines, cur = [], [], []
    for word in text.split():
        # A word wider than the line (a long hyphenated term) breaks after its hyphens; it used to
        # run off the slide ("time-to-first-sourced-deal" cut at the right edge)
        if "-" in word.strip("-") and draw.textbbox((0, 0), word, font=font)[2] > max_w:
            chunk = ""
            for piece in re.findall(r"[^-]*-|[^-]+$", word):
                if chunk and draw.textbbox((0, 0), chunk + piece, font=font)[2] > max_w:
                    words.append(chunk)
                    chunk = piece
                else:
                    chunk += piece
            words.append(chunk)
        else:
            words.append(word)
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


# Nov 2026: cards and decks are drawn by designs.py (one idea per slide, an icon per point, the
# author's face on the cover); CARD_DESIGN=v1 goes back to the editorial design below, and so
# does any card or deck the new design fails to draw.
CARD_DESIGN = os.getenv("CARD_DESIGN", "v2").strip().lower()
# The icons designs.py draws; a writer names one per point, or none
CARD_ICONS = ("pulse", "alert", "network", "link", "growth", "decline", "money", "clock", "people",
              "target", "check", "shield", "idea", "gear", "calendar", "chat", "search", "doc")


def _v2_look(theme, company: dict | None) -> dict:
    """A card's colours for designs.py, with the brand kit's headline font."""
    t = _ed_theme(theme, _brand_accent(company))
    t["font"] = str(((company or {}).get("brand_kit") or {}).get("font") or "")
    return t


# One house style per author (22 Nov 2026 audit of the best LinkedIn visuals: Poyar's footer bar, McTighe's paper,
# Visualize Value's black and white; "a different style on every post is itself generic"). Without a brand kit an
# author keeps one colour set, chosen from their profile, on every card, diagram and Idea picture. This replaces the
# random set per picture Rishav chose in Oct 2026; CARD_HOUSE_STYLE=0 brings random back.
CARD_HOUSE_STYLE = os.getenv("CARD_HOUSE_STYLE", "1").strip() != "0"


def house_theme(company: dict | None) -> str:
    """The author's own colour set: the one saved on the profile (card_theme), else a stable pick from their id."""
    import hashlib
    keys = list(_ED_THEMES)
    saved = str((company or {}).get("card_theme") or "")
    if saved in keys:
        return saved
    seed = str((company or {}).get("id") or (company or {}).get("name") or "")
    return keys[int(hashlib.md5(seed.encode()).hexdigest()[:6], 16) % len(keys)] if seed else keys[0]


def _pick_card_theme(theme: str = "", company: dict | None = None) -> str:
    """The colour set asked for; else the author's own brand look when the profile has a brand kit
    (their deck's look first); else the author's house colour set (or, with CARD_HOUSE_STYLE off, any at random)."""
    import random
    if theme in _ED_THEMES or (theme in BRAND_LOOKS and _brand_kit_theme(company, theme)):
        return theme
    looks = _valid_brand_looks(company)
    if looks:
        own = (company or {}).get("carousel_theme", "")
        return own if own in looks else looks[0]
    if CARD_HOUSE_STYLE and company and (company.get("id") or company.get("name")):
        return house_theme(company)
    return random.choice(list(_ED_THEMES))


def _option_themes(theme: str, n: int, company: dict | None = None) -> list[str]:
    """n different colour sets for side-by-side options, the one asked for first; an author with
    a brand kit gets their other brand looks next."""
    import random
    first = _pick_card_theme(theme, company)
    own = [k for k in _valid_brand_looks(company) if k != first] if first in BRAND_LOOKS else []
    rest = own + random.sample([t for t in _ED_THEMES if t != first], len(_ED_THEMES) - (first in _ED_THEMES))
    return [first] + rest[:max(0, n - 1)]


def _valid_brand_looks(company: dict | None) -> list[str]:
    return [k for k, t in zip(BRAND_LOOKS, _brand_looks(company)) if t]


def _card_look(theme: str, company: dict | None):
    """What a card is drawn in: the brand kit look a brand key names, else the colour set's name."""
    return (_brand_kit_theme(company, theme) or "light") if theme in BRAND_LOOKS else theme


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
    action_label = str(kit.get("action_label") or "").strip()[:24]
    logos = {}                 # "light"/"dark": the logo made for light / dark backgrounds
    for key, b64 in ((kit.get("logo") or {}) if isinstance(kit.get("logo"), dict) else {}).items():
        try:
            logos[key] = Image.open(io.BytesIO(base64.b64decode(b64))).convert("RGBA")
        except Exception:
            logging.warning(f"[Carousel] brand kit logo '{key}' could not be read")
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
                     cover="dark" if look.get("cover") == "dark" else "", site=site,
                     logo=logos, action_label=action_label)
        out.append(t)
    return out


def _ed_logo(img, t: dict, y: int):
    """A brand kit's logo at the top right, in the version made for this slide's background;
    none on a slide in the accent colour, where a mark in that colour would vanish."""
    logos = t.get("logo") or {}
    logo = logos.get("dark" if _luminance(t["bg"]) < 0.5 else "light")
    if logo is None or t["bg"] == t["accent"]:
        return
    h = 56 * _SCALE
    w = int(logo.width * h / logo.height)
    mark = logo.resize((w, h), Image.LANCZOS)
    img.paste(mark, (_RW - 84 * _SCALE - w, y), mark)


def _brand_kit_theme(company: dict | None, look: str) -> dict | None:
    """The brand kit look a carousel_theme value names ("brand", "brand-2", "brand-3"), if valid."""
    looks = _brand_looks(company)
    i = BRAND_LOOKS.index(look) if look in BRAND_LOOKS else len(looks)
    return looks[i] if i < len(looks) else None


def _ed_theme(theme, brand_accent: tuple | None = None) -> dict:
    if isinstance(theme, dict):   # a brand kit look: the author's own colours as they are
        return dict(theme)
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
    # A one-word label is no label ("First: time-to-first-sourced-deal" showed as "First"): the whole point
    pts = [(lab if len(lab.split()) > 1 else f"{lab}: {desc}") if lab else desc for lab, desc in _ed_points(points)]
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
# box) in front of the pasted source, which main.py has already cut to 6,000. Cutting the whole
# thing at 2,000 here left none of the source for anyone with a long knowledge box, and 2,000 of
# an article is its opening only: a deck from an author's own article contradicted its second half.
_FROM_TEXT_MAX = 9000


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


# A one-line opinion goes on a handwritten note (22 Nov 2026 research, 12 creators / 650 image posts against each
# creator's own median: handwritten note 1.2-1.9x, designed statement card ~0.95x, tweet-screenshot card 0.5-0.7x).
# NOTE_CARD=0 puts the statement card back as Auto's line card.
NOTE_CARD = os.getenv("NOTE_CARD", "1") != "0"
_LINE_CARD = "note_card" if NOTE_CARD else "statement_card"
# The author's own photo goes out untouched (the same study: real photo with no text 1.2-1.45x, photo with a text
# overlay 0.9-1.0x). YOUR_PHOTO_LINE=1 puts the post's line back on it.
YOUR_PHOTO_LINE = os.getenv("YOUR_PHOTO_LINE", "0") == "1"
TWEET_CARD_STYLE = os.getenv("TWEET_CARD_STYLE", "post")   # "post" (a real post, round 2) or "statement" (the poster)


def _post_mode(theme: str, company: dict | None) -> str:
    """'dark' or 'light' for the post card: the explicit choice, else the author's house look's lightness."""
    if theme in ("dark", "light"):
        return theme
    try:
        return "light" if _luminance(_v2_look(_card_look(theme, company), company)["bg"]) > 0.5 else "dark"
    except Exception:
        return "dark"


def render_statement_card_png(line: str, company: dict | None, author: dict | None = None, theme: str = "",
                              meta: dict | None = None, style: str = "") -> bytes:
    """A hook line as the post's card (the "tweet card" format): 4:5, in the author's brand look (else a colour
    set), signed with their LinkedIn photo at the top. The old black strip is drawn only if this fails."""
    brand = (company or {}).get("name", "Voyce")
    name, avatar = _card_author(brand, author)
    title = (company or {}).get("designation", "")
    theme = _pick_card_theme(theme, company)
    img = None
    if TWEET_CARD_STYLE == "post" and style != "statement":
        # a real post (22 Nov 2026 audit, round 2): near-black or off-white by the author's house look
        try:
            import designs
            mode = _post_mode(theme, company)
            img = designs.post_card(mode, line, name, avatar)
            theme = mode
        except Exception:
            logging.exception("[Carousel] post card failed; drawing the statement card")
            img = None
    line = " ".join((line or "").split())   # the statement card and the old strip set one paragraph
    setup, turn = _split_turn(line, "")
    emph = turn if setup and len(turn.split()) <= 9 else ""   # a long turn in the accent is a wall of colour
    try:
        if img is None:
            import designs
            img = designs.statement_card(_v2_look(_card_look(theme, company), company), line, emph, name, title, avatar)
    except Exception:
        logging.exception("[Carousel] statement card failed; drawing the old strip")
    if img is None:
        img, theme = _slide_tweet_card(line, name, title, _get_palette(company), rw=IMG_POST_W * _SCALE,
                                       avatar=avatar), ""
    if meta is not None:
        meta.update(format="tweet_card", theme=theme)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def render_image_post_png(content: dict, company: dict, author: dict | None = None,
                          theme: str = "", meta: dict | None = None) -> bytes:
    """The quote card at 4:5: the one line, signed by its author, in the colour set asked for
    (else a random one). meta, when given, gets what was drawn: format and colour set."""
    brand = (company or {}).get("name", "Voyce")
    name, avatar = _card_author(brand, author)
    theme = _pick_card_theme(theme, company)
    if meta is not None:
        meta.update(format="quote_card", theme=theme)
    sub = content.get("card_subtext", "")
    if name and name.split()[0].lower() in sub.lower():   # the signature under it already names them
        sub = ""
    img = None
    if CARD_DESIGN == "v2":
        try:
            import designs
            img = designs.quote_card(_v2_look(_card_look(theme, company), company), content.get("card_headline", ""),
                                     content.get("card_emphasis", ""), sub, content.get("card_tag", ""), name,
                                     (company or {}).get("designation", ""), avatar)
        except Exception:
            logging.exception("[Carousel] quote card v2 failed; drawing the editorial one")
    img = img or _ed_quote_card(
        content.get("card_headline", ""), sub, content.get("card_emphasis", ""),
        content.get("card_tag", ""), name, (company or {}).get("designation", ""), avatar,
        theme=_card_look(theme, company), brand_accent=_brand_accent(company),
        rw=IMG_POST_W * _SCALE, rh=IMG_POST_H * _SCALE,
    )
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


# ── AI image post (research-grounded) ────────────────────────────────────────
# Research (deep-research, 2026): AI human faces/hands are the #1 credibility killer and
# "AI slop" tell. Until Oct 2026 that meant flat illustrations only; owners' own posts are
# photographic, so scenes are realistic editorial photos with no visible faces and no text.
# Nov 2026: a meeting-room photo fits any post, so it said nothing (three runs on one post gave
# the same two people and a blank sheet). The image model now spells what it draws, so a post
# that argues a point gets a concept picture: one metaphor in real objects that makes the
# argument, with its headline drawn on, the way authors make theirs in ChatGPT. Claude checks
# every drawn image and the image model repairs what it finds (_checked_image).

_AI_IMAGE_SYSTEM = """You design the VISUAL for a founder's LinkedIn post, plus the caption.

FORMAT — decide this FIRST, in two steps. The image must never repeat the caption — keep any text on it
SHORT.
STEP 1, post_shape: what the post IS, judged from its body, not its topic:
- "list": it gives 3 or more parallel items: steps, checks, rules, controls, mistakes, a framework.
- "contrast": it sets two sides against each other: what most do vs what works, before vs after,
  myth vs truth, old way vs new way.
- "tradeoff": it weighs options on two dimensions (effort vs impact, risk vs reward, cost vs speed).
- "stat": one number from the post, with its named source, IS the story.
- "numbers": the post compares 2-6 figures it states (shares, costs, before/after amounts) with a source.
- "range": it says where something sits between two ends (on paper vs enforced, guess vs measured).
- "overlap": the point is where two things meet (what you know and what they can repeat).
- "gap": two things pull apart over time and the gap between them is the point.
- "self_check": it asks readers to judge themselves (signs you..., how many of these..., is your X ready).
- "options": it compares 2-4 named options (tools, approaches, models, roles) across several criteria.
- "journey": it tells how the author (or a company) got here through dated milestones over years.
- "claim": it builds to one sharp, quotable line.
- "mechanism": it explains HOW something works or WHY it fails: a cause and its effect, how a thing plays out
  early vs later, or what most people do vs what works, as something you could sketch on a napkin.
- "argument": it argues a point or names a mistake that one visual metaphor can make at a glance.
- "moment": it tells a real moment the author lived (a visit, an event, a meeting).
STEP 2, format, from post_shape (follow this table; a picked format the table doesn't give is wrong):
- list → "diagram" with diagram_kind "ladder" when its points can be read as levels from worse to better
  practice (most advice lists can: what weak teams do, up to what the best do), "pairs" when they are separate
  fixes, each for a problem the reader would admit to, "steps" when they are a strict sequence; "notebook" only for
  a framework of named parts that is none of these. Checks to tick off before something are a ladder too: from the
  team that has none of them in place up to the team that has them all. For any list post fill dg_levels AND
  dg_pairs, whichever kind you pick (22 Nov 2026: plain numbered lists and checklists are the least-saved visual;
  ladders and pairs are among the most saved, and scored 7 to the plain checklist's 5-6 on the same posts).
- contrast → "diagram" with diagram_kind "compare" (or "before_after" when the post shows ONE thing
  rewritten or changed: a line, a page, an offer, a process, best with its result).
- self_check → "diagram" with diagram_kind "scorecard".
- options → "diagram" with diagram_kind "table".
- journey → "diagram" with diagram_kind "timeline".
- tradeoff → "diagram" with diagram_kind "matrix".
- stat → "diagram" with diagram_kind "stat".
- numbers → "diagram" with diagram_kind "chart".
- range → "diagram" with diagram_kind "spectrum".
- overlap → "diagram" with diagram_kind "venn".
- gap → "diagram" with diagram_kind "gap".
- claim → "note_card" when the line stands alone (most claims and hot takes), or "page_card" when it lands
  hardest after two or three sentences of setup.
- mechanism → "explainer".
- argument → "concept".
- moment → "your_photo" when the request says the author has photos, else "note_card" (never an AI photo of
  a person: in the 22 Nov 2026 image audit stock-like photos were the weakest picture of all).
When a post fits two shapes, take the one its middle is built on (a post that lists four controls is
a list, even if it opens with a claim).
The formats:
- "concept": ONE visual metaphor built from ordinary physical things that makes the post's argument
  at a glance, with a two-part headline drawn on the image (see CONCEPT below).
- "notebook": a photo of a real spiral notebook with the post's framework hand-drawn on the page as a
  colourful, save-worthy sketchnote (highlighted headings, a small icon each). nb_title = the
  framework's name in max 6 words; nb_items = 3-5 headings, each max 4 words; nb_lines = one short
  line under each heading, max 7 words, same order (what it means or what to do); nb_style =
  "sections" (default), or "flow" (boxes joined by arrows) for a strict sequence of very short steps;
  nb_footer = the takeaway in max 7 words. Max 60 words in all, every word one the image model can
  spell (no codes, no long brand names).
- "object_quote": card_headline = ONE line from the post, max 12 words (max 8 reads best), that
  stands alone; card_emphasis = 1-3 of its words to underline. Voyce chooses the object.
- "diagram": drawn by Voyce, so the words are always exact. diagram_kind and its fields:
  "steps": dg_title (max 8 words) + dg_items (3-5 steps, each max 6 words, starting with a verb).
  "compare": dg_title + dg_left_head / dg_right_head (2-3 words each, e.g. "Most teams" / "What
    works") + dg_left / dg_right (3 rows each, max 6 words, row 1 left answers row 1 right).
  "matrix": dg_title + dg_x / dg_y (each axis's name, max 5 words, low to high) + dg_cells (4 cells,
    max 4 words each, in order top left, top right, bottom left, bottom right) + dg_pick (0-3: the
    cell the post argues for).
  "stat": dg_number (exactly as the post gives it, e.g. "77%") + dg_line (what it means, max 12
    words) + dg_source (who published it and the year, as the post names it) + dg_title (a 1-3 word
    topic label). Only a number the post states with its source; never a number you made up.
  "chart": dg_title = the CLAIM the numbers prove, as a sentence (max 12 words: "Those cutting SDRs
    are hiring SDRs", not "SDR hiring data") + dg_bars (2-6 of {"label": max 4 words, "value": exactly
    as the post states it, e.g. "28%"}) + dg_pick (the bar the claim is about) + dg_source.
  "spectrum": dg_title (the claim or question) + dg_from / dg_to (the two ends, max 3 words each) +
    dg_stops (3-5 points along it, max 4 words each, from the dg_from end) + dg_pick (the stop the
    post argues for).
  "venn": dg_title + dg_a / dg_b (the two things, max 4 words each) + dg_overlap (what sits where they
    meet: the post's point, max 6 words).
  "gap": dg_title (the claim) + dg_top (the line that climbs, max 4 words) + dg_bottom (the line that
    stays low, max 4 words) + dg_gap (the name of the gap: the point, max 4 words) + dg_x (the time
    span or axis, max 4 words, may be empty).
  "ladder": hand-lettered levels the reader places themselves on. dg_title = a question the reader answers
    about themselves (max 7 words: "How ready is your migration plan?") + dg_levels (3-5, from WORST to BEST, each
    max 11 words, what a team at that level would say, in quotes ("We'll convert the code and see what breaks."),
    or a plain habit; each clearly better than the one below, built only from the post's points) + dg_from /
    dg_to (the bottom and top labels, 2-3 words: "Least ready" / "Most ready").
  "pairs": hand-lettered "when this -> do that". dg_pairs_title (2-4 words naming the moment: "Before AWS
    validation", never "Tips" or "Checklist") + dg_line (optional, when it applies, max 8 words) + dg_pairs (4-6
    of {"when": 1-3 words, a symptom the reader would admit to, "do": 1-3 words, an imperative}: short like
    "OVERTHINKING -> WRITE", never a sentence; under 30 words in all).
  "checklist": dg_title (what it checks, max 8 words: "Before an agent touches production") + dg_items
    (4-7 checks, each max 8 words, each a thing the reader can tick off as true) + dg_line (an optional
    footer, max 8 words, e.g. "Tick all five before go-live").
  "before_after": dg_title (the claim) + dg_left (the BEFORE, the actual line or thing, max 18 words) +
    dg_right (the AFTER, max 18 words) + dg_left_head / dg_right_head (optional, default Before / After) +
    dg_before_num / dg_after_num (the result before and after, exactly as the post states them, else
    empty) + dg_num_label (what the number measures, max 3 words) + dg_source.
  "scorecard": dg_title (the question, max 8 words: "Is your layoff number a diagnosis?") + dg_items
    (4-6 statements, each max 10 words, written so YES is the good answer) + dg_bands (3 strings, the
    score bands from 0 to the number of statements, e.g. "0-1: A guess with a deck", "2-3: Half a
    diagnosis", "4-5: A real plan"; each label max 5 words).
  "table": dg_title (the claim) + dg_cols (2-4 options, max 3 words each) + dg_rows (3-6 criteria, max 5
    words each) + dg_marks (one mark per row per option, row by row: "yes", "no", "part" or max 2 words)
    + dg_pick (the option, 0-based, the post argues for).
  "timeline": dg_title (the turn in the story, max 10 words) + dg_events (3-6 of {"when": the year or
    date exactly as given, "what": max 8 words}, oldest first) + dg_pick (the turning point, 0-based).
    ONLY dates and facts the post or the author's profile states: never a year you worked out or made up.
  In every diagram the shape IS the argument: one accent on the one element that matters.
- "explainer": a hand-drawn explainer sketch in the style of Sketchplanations: simple black ink lines on warm
  paper that show the post's MECHANISM, with a few hand-lettered labels and ONE red accent on the thing to
  notice. ex_title = max 6 words; ex_scene = 2-4 sentences saying exactly what is drawn where: one simple scene,
  or two panels side by side with short headers (e.g. "WHAT BOARDS HEAR" / "WHAT YOU GET", "EARLY" / "LATER");
  ordinary objects, simple stick figures, speech bubbles; specific to THIS post. ex_labels = 2-6 hand-lettered
  labels, max 4 words each, every word easy to spell (the panel headers and any speech-bubble words count as
  labels); ex_accent = what gets the red accent. Never lightbulbs, rockets, gears, puzzle pieces, handshakes,
  chess, robots, brains, mountains or charts going up.
- "your_photo": the author's own photo, posted as it is (no text on it: the caption tells the story);
  card_headline = the post's point in max 9 words (used only when the account asks for a line on its photos),
  card_emphasis = 1-3 words of it.
- "photo_card": a realistic PHOTOGRAPH of the moment (fill image_concept) with ONE short line under
  it: card_headline = the post's point in max 9 words, card_emphasis = 1-3 words of it.
- "scene": the same kind of photo with no text, when the picture alone says it.
- "list_card": a headline (max 9 words) + 3-4 SHORT points in card_points, each a 2-6 word label with
  NO explanation (the caption explains them), and card_icons: for each point, the icon that fits it
  from pulse, alert, network, link, growth, decline, money, clock, people, target, check, shield,
  idea, gear, calendar, chat, search, doc ("" when none fits).
- "quote_card": ONE sharp quotable claim on a designed card (card_headline max 12 words).
- "tweet_card": a HOT TAKE — contrarian, a little confrontational → shown as a real post with the
  author's photo and name (card_headline = the one-line version, max ~30 words). Use for the boldest,
  most argument-starting claims. tweet_text = the take as the author would post it: 14-45 words in their
  plain voice. A first line that lands on its own, then 1-2 short paragraphs, or a numbered mini-story
  (one item per line), with a blank line between blocks (use \\n\\n between blocks, \\n only between list
  items). Never one sentence per line: a stack of one-line sentences reads as AI. No hashtags, emojis,
  quotation marks, bold or a question to the reader at the end.
- "note_card": the post's one line written by hand in pen on plain paper, signed small (like Sahil Bloom's
  and Gary Vaynerchuk's handwritten notes): card_headline = the line, 8-22 words, complete and quotable on its
  own, in the author's plain words; card_emphasis = 1-2 of its words to underline (the turn) or "".
- "page_card": a cropped printed page: page_setup = two or three short sentences (30-55 words) that lead up to
  the claim, in the author's voice, only what the post says; card_headline = the claim, 4-12 words, set in bold
  as the page's last line.
- "statement_card": ONE principle the author stands by, as a single big sentence on a plain card (like
  Steven Bartlett's "If you don't care about tiny details you'll produce bad work..."): card_headline = the
  sentence, 8-22 words, complete and quotable on its own; card_emphasis = its turn (max 9 words) or "".

CONCEPT (format "concept") — it has to make a stranger stop scrolling and get the point in two
seconds on a phone, so it is as good as anything the author would make by hand:
card_headline: two short sentences, the setup then the turn, max 12 words in all, in the author's
  plain words. The setup names the reader or their situation; the turn is the surprise, the
  reframe or the cost, and it lands hard (e.g. "Your CXO isn't reading the menu. They're ordering
  one number."). No question marks, no "Here's why", no colon labels, no hashtags.
card_emphasis: the turn, exactly as it appears at the end of card_headline (drawn in the accent colour).
image_concept: 2-4 sentences telling an illustrator exactly what to draw: the objects, how they
  sit, which one wins the eye, and where each piece of text goes on them. ONLY the objects: never
  the headline (we set it), and never a desk, table, wall or other surface: the objects rest on a
  plain flat background with a soft shadow.
image_words: every word written on the objects, as a list of short strings, max 12 words in all
  (every extra word is one more the image model can misspell). Each string sits whole on one
  object: never one word spread across two objects.
How good concept pictures work:
- Before answering, think of three different metaphors and keep the one a stranger would get
  fastest. If it would fit any post on the topic, it is not sharp enough.
- BUILD IT ON THE READER'S OWN ARTIFACT. First name reader_artifact: one specific thing only THE
  READER handles in their job, in their own terms (an AWS Competency validation checklist, an RBI
  inspection report, a quarterly board pack, a creator's rate card on a campaign brief, a CFO's
  variance sheet, a placement drive schedule). It is the hero of the picture, recognisable at a
  glance, carrying the post's tension. The reader should think "that's my desk". Generic office
  props on their own (box files, folders, magnifying glasses, rubber stamps, sticky notes, receipts,
  menus, coffee cups, tangled cables) are not enough: they may only support the reader's artifact.
- Keep it simple enough to draw cleanly: one hero object, at most two supporting ones, and only the
  words in image_words. A busy scene with many objects or small print fails the check. A document
  is recognised by its shape and a large title or label (in image_words); everything else on it is
  plain lines, never body text, small print or a signature. The stock ones (a menu and an order slip, a receipt, a boarding pass, a to-do
  list, a sticky note, a price tag, a luggage label) only when nothing from the post's world works:
  every author's pictures leaning on the same ten objects reads as a template.
- At most one hand-drawn circle or underline, and only when the metaphor needs it.
- Few words: a metaphor that reads with 0-6 words beats a sheet, form or list full of writing,
  which the image model letters as gibberish (a checklist with handwritten notes failed three
  drawings in a row on 22 Nov 2026).
- It carries the contrast or tension in THIS post: what people do vs what works, the many vs the
  one, the cost vs the price. The headline and the picture say the same thing.
- Things carry SHORT, real words that make the metaphor readable (a few list items, a circled
  line, a handwritten note). Numbers come from the post; never invent a statistic for the picture.
- A hand is fine (if an AUTHOR'S REGION is given, from there); no people, no faces.
- Never: robots, brains, circuits, lightbulbs, rockets, chess pieces, puzzle pieces, handshakes,
  arrows, ladders, targets, mountains, globes, screens showing apps.

PHOTO (photo_card and scene) — a decorative metaphor is NOT a photo; describe a real moment.
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
  THE READER IN THE FRAME: the people are THE READER at work (their role, their kind of office or site), so a
  prospect scrolling past sees their own day.
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

Return ONLY JSON, with the keys the request lists."""


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
    if v < 0.16:  # charcoal (22, 24, 29) is near-black, not the "deep blue" its tint made it
        return "near-black"
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


def _build_ai_image_prompt(concept: str, p: dict, region: str = "", ratio: str = "4:5") -> str:
    # Realistic editorial photography (was a flat illustration until Oct 2026: owners' own posts are
    # photographic, and a cartoon beside a B2B post read as clip art).
    shape = "Vertical 4:5 composition." if ratio == "4:5" else f"Landscape {ratio} composition."
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
        "NO grinning stock models. " + shape
    )


def _split_turn(headline: str, emphasis: str) -> tuple[str, str]:
    """A concept headline as (setup, turn): the turn is the emphasis when the headline ends with it,
    else the last sentence."""
    head = (headline or "").strip()
    turn = (emphasis or "").strip()
    i = head.lower().rfind(turn.lower()) if turn else -1
    if i > 0:
        return head[:i].strip(), head[i:].strip()
    parts = re.split(r"(?<=[.!?])\s+", head)
    return (" ".join(parts[:-1]), parts[-1]) if len(parts) > 1 else (head, "")


# How an Idea picture is made. Every one used to be paper objects photographed on a flat colour, often with a
# red marker circle: good alone, a template by a follower's second week (21 Nov 2026 audit). A profile's
# pictures take the looks in CONCEPT_LOOK_ROTATION in turn (_next_concept_look), each on the flat background
# our headline needs. "sketch" (a hand-drawn marker drawing) tested well on 22 Nov but is an illustration, and
# Rishav chose realistic pictures over illustrations in Oct 2026: it is off until he says yes
# (CONCEPT_LOOKS=still,sketch). A cut-paper look was tried and dropped: the model drew a photo still life anyway.
CONCEPT_LOOKS = {
    "still": ("The objects look like real printed paper, card, plastic and ink, photographed as a still life with a "
              "soft natural shadow, slightly tilted, crisp and premium, large and close enough to read on a phone."),
    "sketch": ("Drawn as one confident hand-made sketch straight onto the flat background, like a strategist's "
               "drawing on a board: thick marker-style lines in {ink} with the {accent} accent, simple bold shapes, "
               "hand-lettered words in clear capitals, generous line weight so it reads on a phone. No sheet of "
               "paper, no board edge, no photo: the lines sit on the background itself."),
}
# The metaphor picture's own look (round 2 research: Aaron Tilley's and Sarah Illenberger's conceptual still lifes,
# the style the best editorial metaphors use): never in the rotation, only for concept_metaphor's drawing.
CONCEPT_LOOKS["metaphor"] = (
    "Photographed like a conceptual studio still life by Aaron Tilley: the impossible part is built by hand and "
    "photographed with exactly the same realism as everything else. One hard key light from the upper left throws a "
    "crisp shadow to the lower right; medium-format camera, 80mm lens, eye level, f/8, everything sharp. One simple, "
    "clean hero object, large in the frame, in real materials.")
CONCEPT_LOOK_ROTATION = tuple(k for k in os.getenv("CONCEPT_LOOKS", "still").replace(" ", "").split(",")
                              if k in CONCEPT_LOOKS) or ("still",)


def _next_concept_look(company: dict | None) -> str:
    """The look after the one this profile's last Idea picture used (stored on the profile)."""
    keys = list(CONCEPT_LOOK_ROTATION)
    last = str((company or {}).get("concept_look") or "")
    return keys[(keys.index(last) + 1) % len(keys)] if last in keys else keys[0]


def _remember_concept_look(company: dict | None, look: str) -> None:
    try:
        if (company or {}).get("id"):
            import db
            db.companies.update_one({"id": company["id"]}, {"$set": {"concept_look": look}})
            company["concept_look"] = look
    except Exception:
        logging.exception("[Image] could not store the picture's look")


def _build_concept_prompt(content: dict, t: dict, region: str = "", look: str = "still") -> str:
    """The image model's brief for a concept picture: the metaphor in the lower part of the frame,
    on a flat background in the card's colours, with the top left empty for the headline we set."""
    bg = "#%02X%02X%02X" % tuple(int(x) for x in t["bg"][:3])
    words = "; ".join(f'"{w}"' for w in content.get("image_words") or [])
    medium = CONCEPT_LOOKS.get(look, CONCEPT_LOOKS["still"]).format(
        ink=_describe_color(t["ink"]), accent=_describe_color(t["accent"]))
    return (
        f"A vertical 4:5 editorial concept image for a LinkedIn post. Flat solid background colour {bg} "
        f"filling the whole frame, no gradient, no texture, no vignette. The TOP 42% of the frame is "
        f"completely EMPTY background: no objects, no hands, no shadows, no text, nothing (a headline is "
        f"added there later; do not draw any headline or title). LOWER 58%: {content.get('image_concept', '')} "
        f"{medium} The objects sit DIRECTLY on the flat {_describe_color(t['bg'])} background, which runs "
        f"edge to edge behind them: no white or light sweep under them, no desk, no table, no wall, no "
        f"frame, no inset photo, no panel, no box, no border, no second background colour. "
        + (f"Any hand belongs to a person from {region}. " if region else "")
        + (f"The only words in the image are these, written on the objects: {words}. Spell every word "
           f"exactly as given; each one sits whole on a single object, never split across two, never "
           f"repeated. " if words else "There are no words anywhere in the image. ")
        + "Any document, form, card or screen-free page shows only those words, as large titles or labels; "
        "all its other content is plain light-grey lines with no letters at all, never small print or a signature. "
        + f"No other text, no logos, no watermark, no people, no faces. A restrained palette: the "
        f"background colour, {_describe_color(t['ink'])} and {_describe_color(t['accent'])} accents, and "
        f"natural paper and material tones. (Colour codes in this brief are values, never words to draw.)"
    )


_DISPLAY_FONTS = {"poppins": "Poppins-ExtraBold.ttf",   # brand_kit["font"] → the display weight we hold
                  "montserrat": "Montserrat-Bold.ttf", "instrument sans": "InstrumentSans-Bold.ttf"}   # OFL, Google Fonts
_CONCEPT_TURN = {"light": (190, 72, 32), "sage": (38, 112, 66)}   # concept turn colour where the accent is near the ink


def _display_font(size: int, key: str = ""):
    """The headline face: the author's brand font when we hold it, else Inter Black."""
    name = _DISPLAY_FONTS.get((key or "").strip().lower())
    if name:
        try:
            return ImageFont.truetype(os.path.join(_FONTS_DIR, name), int(size))
        except Exception:
            pass
    return _font_black(size)


def _free_top(img: Image.Image) -> tuple[float, bool, tuple]:
    """How far down (0..1) the picture's top stays plain background (the first row, scanning down,
    where more than 2% of the pixels differ clearly from the colour at the very top), and whether
    the objects sit in a box of another colour: near both side edges, down the lower half, the
    background colour mostly gone (the model likes a white sweep under objects on a dark colour),
    and the background colour as drawn."""
    small = img.convert("RGB").resize((108, 135), Image.BILINEAR)
    px = small.load()
    far = lambda c, bg: sum(abs(c[i] - bg[i]) for i in range(3)) > 60
    top = [px[x, y] for y in range(2) for x in range(108)]
    bg = tuple(sorted(c[i] for c in top)[len(top) // 2] for i in range(3))
    free = next((y / 135 for y in range(135) if sum(1 for x in range(108) if far(px[x, y], bg)) > 2), 1.0)
    edges = [px[x, y] for y in range(74, 128, 6) for x in (6, 9, 98, 101)]
    boxed = sum(1 for c in edges if far(c, bg)) >= 0.75 * len(edges)
    return free, boxed, bg


def _lower_picture(img: Image.Image, free: float, bg: tuple, target: float = 0.40) -> Image.Image:
    """A drawing that reaches into the headline's space, scaled down to the bottom centre of a frame in its own
    background colour (a flat colour by design), its edges feathered so no seam shows. Such drawings used to be
    thrown away, and a post asking for an Idea picture got a plain card (21 Nov 2026 audit). No smaller than
    0.55 of the frame: below that the words on the objects stop reading on a phone."""
    from PIL import ImageFilter
    w, h = img.size
    s = (1 - target) / max(0.01, 1 - free)
    if s < 0.55:
        raise ValueError(f"the picture starts {free:.0%} down; it would shrink too far to read")
    small = img.convert("RGB").resize((int(w * s), int(h * s)), Image.LANCZOS)
    feather = max(8, int(min(small.size) * 0.05))
    mask = Image.new("L", small.size, 0)
    ImageDraw.Draw(mask).rectangle([feather, feather, small.width - feather, small.height - 1], fill=255)
    mask = mask.filter(ImageFilter.GaussianBlur(feather / 2))
    out = Image.new("RGB", (w, h), tuple(int(c) for c in bg[:3]))
    out.paste(small, ((w - small.width) // 2, h - small.height), mask)
    return out


def _letter_concept(pic: Image.Image, content: dict, t: dict, footer: str, font_key: str = "") -> Image.Image:
    """Voyce sets the concept picture's type, so it is the same on every post: the headline in the
    space the picture left at the top, as large as fits in up to 5 lines (the setup in the ink, the
    turn on its own line in the look's mark), the author's site or name small at the bottom right."""
    S = _SCALE
    rw, rh = IMG_POST_W * S, IMG_POST_H * S
    img = _cover_crop(pic, rw, rh)
    free, boxed, bg = _free_top(img)
    if boxed:
        raise ValueError("the objects sit in a box of another colour, not on the background")
    if free < 0.27:   # the drawing reached into the headline's space: move it down instead of losing it
        img = _lower_picture(img, free, bg)
        free, boxed, bg = _free_top(img)
    if free < 0.27:   # type on it would collide
        raise ValueError(f"the picture starts {free:.0%} down, no room for the headline")
    draw = ImageDraw.Draw(img)
    pad, top, gap = 84 * S, 92 * S, 10 * S
    max_w = rw - pad * 2
    bottom = min(int(rh * free) - 36 * S, int(rh * 0.46))
    setup, turn = _split_turn(content.get("card_headline", ""), content.get("card_emphasis", ""))
    font = None
    for s in (108, 100, 92, 84, 76, 70, 64, 58, 52):
        font = _display_font(s * S, font_key)
        h = sum(_ed_block_h(draw, part, font, max_w, gap) + gap for part in (setup, turn) if part)
        if h <= bottom - top and sum(len(_wrap(draw, part, font, max_w)) for part in (setup, turn) if part) <= 5:
            break
    y = top
    if setup:
        y = _ed_draw_marked(draw, setup, font, pad, y, max_w, t, "", gap) + gap
    if turn:
        _ed_draw_marked(draw, turn, font, pad, y, max_w, t, turn, gap)
    if footer:   # on a strip of the background, so it reads where the picture reaches the corner
        f_foot = _font(26 * S, semi=True)
        fw, fh = draw.textlength(footer, font=f_foot), _ed_line_h(draw, f_foot)
        fx, fy = rw - pad - fw, rh - 64 * S - fh
        draw.rounded_rectangle([fx - 18 * S, fy - 12 * S, fx + fw + 18 * S, fy + fh + 14 * S], radius=10 * S, fill=bg)
        draw.text((fx, fy), footer, font=f_foot, fill=t["muted"])
    return img.resize((IMG_POST_W, IMG_POST_H), Image.LANCZOS)


_IMAGE_CHECK_SYSTEM = """You are the art director who signs off an AI-made image before it goes on
someone's LinkedIn post. Judge it as a person scrolling the feed on a phone would see it, and be
strict: the author should never need to make the image somewhere else.

Report only BLOCKERS, the faults a viewer would notice in two seconds or that make the image wrong:
1. WORDS: a word misspelt, garbled, doubled or unreadable, gibberish lettering, or text that was
   not asked for (a stray code, a second copy of the headline or footer).
2. MEANING: a stranger would get the post's point wrong, or not at all.
3. READING: the main words can't be read on a phone, or something sits on top of the headline.
4. MISTAKES: hands or fingers that look wrong, melted or impossible objects, brand logos,
   watermarks, a frame, inset photo or desk behind the objects when a plain background was asked for.
Not blockers: one object partly covering another the way real things do, a word partly hidden
behind a hand or a note when it still reads, small details, and anything that is a matter of taste.

Return JSON: {"ok": true|false, "problems": ["..."], "fix": "..."}
ok is true when there is no blocker. When it is false, "fix" is ONE short instruction an image
editor can carry out on this same image to repair exactly those blockers (e.g. "Change the word
'Intergrations' on the green tab to 'Integrations'." or "Remove the second copy of the headline."),
never a new idea."""


def _small_jpeg(im: Image.Image, edge: int = 1500) -> bytes:
    """The image at a size the checker reads well, without paying for every pixel."""
    im = im.convert("RGB")
    im.thumbnail((edge, edge), Image.LANCZOS)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=88)
    return buf.getvalue()


# ── the craft gate (22 Nov 2026 image-type audit) ───────────────────────────────────────────────────────────────
# A Claude judge scores a finished picture 1-10 for top-level vs generic, seeing only the image. Proven first on 42
# labelled images (our research): it sorted 41 correctly, every generic one (stock clichés, AI desks, slogans on
# billboards) 2-4, editorial artists 8-10. Craft isn't performance (viral plain visuals scored 4-5), so it gates only
# AI-drawn pictures, with a floor per format; exact drawn cards never go through it. A judge that can't answer lets
# the picture through. IMAGE_JUDGE=0 turns it off.
IMAGE_JUDGE = os.getenv("IMAGE_JUDGE", "1") != "0"
JUDGE_MIN = {"concept": 7, "explainer": 6, "notebook": 5}

_CRAFT_SYSTEM = """You are a strict art director for senior B2B LinkedIn creators (consultants, founders, CXOs). You judge ONE
image the way a busy senior reader sees it on a phone in the feed: is it top-level, the kind that stops the scroll and
gets saved, or generic, the kind that reads as a template, stock or AI filler and gets scrolled past?
Score 1-10:
9-10: one clear idea made visible in a memorable, crafted way (an Economist cover, an Aaron Tilley still life, a
  hand-drawn framework a reader would save); nothing decorative; specific, not interchangeable.
7-8: clearly made for a point, crafted and readable, no clichés; you'd post it.
5-6: competent but template or stock in feel; interchangeable with a thousand posts.
1-4: generic: stock clichés (handshakes, rockets, lightbulbs, chess pieces, glowing brains, robots, mountains/flags,
  puzzle pieces), 3D mascots or clay figures for no reason, purple/blue gradients and glow, a cosy desk with coffee,
  plant and laptop, a slogan pasted on a billboard or object, AI sheen, decoration unrelated to any point, text too
  dense or small to read on a phone, misspellings.
Judge the image itself, not whether you agree with its message."""


def _craft_score(im) -> int | None:
    """The judge's 1-10 for a finished picture; None when it can't answer (then nothing is blocked)."""
    if not IMAGE_JUDGE:
        return None
    try:
        from llm import look_json
        r = look_json('Return JSON: {"score": 1-10, "verdict": "top|ok|generic", "why": "one sentence"}',
                      _small_jpeg(im.copy(), 1100), system=_CRAFT_SYSTEM) or {}
        s = r.get("score")
        return int(s) if isinstance(s, (int, float)) else None
    except Exception:
        return None


def _checked_image(prompt: str, check: str, ratio: str = "4:5", size: str = "1K", compose=None, quick: bool = False,
                   judge_min: int = 0):
    """Draw an image (a second drawing if the first fails or can't be finished), finish it
    (compose: our type on top), and have Claude look at the finished image against what was
    asked. When it finds a problem the image model repairs that one thing on the same drawing and
    Claude looks again. The PIL image, or None when no drawing passes, so the caller shows a card
    instead of a picture known to be wrong. A check that can't run lets the drawing through.
    judge_min: a picture that passes must also score at least this with the craft judge (above); one that reads as
    generic gets one fresh drawing (none when quick), then None, so the caller's exact fallback is used."""
    from llm import generate_image, look_json

    def _good(im) -> bool:
        if not judge_min:
            return True
        s = _craft_score(im)
        if s is not None and s < judge_min:
            logging.info(f"[Image] craft judge {s} < {judge_min}: reads as generic, not used")
            return False
        return True

    def _finish(b: bytes | None):
        if not b:
            return None
        try:
            im = Image.open(io.BytesIO(b))
            im.load()
            return compose(im) if compose else im
        except Exception as e:   # unreadable, or the drawing left no room for our type
            logging.info(f"[Image] drawing not used: {e}")
            return None

    def _verdict(im) -> dict:
        try:
            return look_json(check, _small_jpeg(im.copy()), system=_IMAGE_CHECK_SYSTEM) or {}
        except Exception:
            return {}

    b = final = None
    for _ in range(2):
        b = generate_image(prompt, aspect_ratio=ratio, size=size)
        final = _finish(b)
        if final is not None:
            break
    if final is None:
        logging.warning("[Image] no usable drawing in 2 tries")
        return None
    verdict = _verdict(final)
    generic = False
    if verdict.get("ok") is not False:
        if _good(final):
            return final
        generic = True   # right, but reads as generic: a repair can't fix that, so straight to a fresh drawing
    if not generic:
        fix = (verdict.get("fix") or "").strip()
        logging.info(f"[Image] check: {verdict.get('problems')} -> repair: {fix}")
        fixed = _finish(generate_image(
            f"Edit this image. {fix} Keep everything else exactly as it is: the same layout, colours, words "
            "and style, and empty space empty.", aspect_ratio=ratio, size=size, edit=b)) if fix else None
        if fixed is not None:
            again = _verdict(fixed)
            logging.info(f"[Image] after repair: {'ok' if again.get('ok') is not False else again.get('problems')}")
            if again.get("ok") is not False and _good(fixed):
                return fixed
    if quick:   # a first try with a fallback behind it (the Idea picture's metaphor): no third drawing
        logging.info(f"[Image] quick try failed the check: {verdict.get('problems') or 'reads as generic'}")
        return None
    # The repair didn't take: one fresh, simpler drawing, kept only if it passes as drawn. Without it 1 in 5
    # Idea pictures came back as a plain card (21 Nov 2026 audit).
    fresh = _finish(generate_image(
        prompt + " Keep it simple: fewer, larger objects, every word large and printed clearly, nothing else.",
        aspect_ratio=ratio, size=size))
    if fresh is not None and _verdict(fresh).get("ok") is not False and _good(fresh):
        return fresh
    logging.warning(f"[Image] no drawing passed the check: {verdict.get('problems') or 'read as generic'}")
    return None


def _concept_image(content: dict, company: dict, look: dict, footer: str):
    """The concept picture for a post: drawn, our type set on it, then checked. Drawn at 1K: side
    by side with 2K at post size the writing on the objects read the same, at two thirds of the
    price and half the time (1 Nov 2026)."""
    words = content.get("image_words") or []
    check = (f"This concept image was made for a LinkedIn post. The headline at the top and the small "
             f"footer are set by Voyce in the author's brand font, so never ask to change their words "
             f"or font; every fix must be to the picture below them.\n"
             f"HEADLINE: \"{content.get('card_headline', '')}\"\n"
             f"BACKGROUND: one flat {_describe_color(look['bg'])} colour edge to edge, the objects on it "
             f"directly (a lighter box, sweep or photo behind them is a blocker)\n"
             f"WORDS ON THE OBJECTS (exactly these, nothing else): {json.dumps(words, ensure_ascii=False)}\n"
             f"WHAT THE PICTURE SHOULD SHOW: {content.get('image_concept', '')}\n"
             f"THE POST IT GOES WITH (start): {(content.get('post_text') or '')[:500]}")
    font_key = str(((company or {}).get("brand_kit") or {}).get("font") or "")
    medium = content.get("concept_look") or _next_concept_look(company)
    img = None
    if content.get("metaphor_concept") and medium == "still":
        # the bold metaphor first (see concept_metaphor), quick: the reader's-artifact picture is behind it
        meta_c = {**content, "image_concept": content["metaphor_concept"], "image_words": content.get("metaphor_words") or []}
        meta_check = check.replace(f"WORDS ON THE OBJECTS (exactly these, nothing else): {json.dumps(words, ensure_ascii=False)}",
                                   f"WORDS ON THE OBJECTS (exactly these, nothing else): "
                                   f"{json.dumps(meta_c['image_words'], ensure_ascii=False)}") \
                         .replace(f"WHAT THE PICTURE SHOULD SHOW: {content.get('image_concept', '')}",
                                  f"WHAT THE PICTURE SHOULD SHOW: {meta_c['image_concept']}")
        img = _checked_image(_build_concept_prompt(meta_c, look, _region_for(company), "metaphor"), meta_check, ratio="4:5",
                             size="1K", compose=lambda pic: _letter_concept(pic, meta_c, look, footer, font_key), quick=True,
                             judge_min=JUDGE_MIN["concept"])
        if img is not None:
            content["concept_used"] = "metaphor"
    if img is None:
        img = _checked_image(_build_concept_prompt(content, look, _region_for(company), medium), check, ratio="4:5",
                             size="1K", compose=lambda pic: _letter_concept(pic, content, look, footer, font_key),
                             judge_min=JUDGE_MIN["concept"])
        if img is not None and content.get("metaphor_concept"):
            content["concept_used"] = "artifact"
    if img is not None:
        _remember_concept_look(company, medium)
    return img


# ── notebook sketchnotes and lines on real objects (Nov 2026) ───────────────────────────────────
# MagicPost's "illustrated notebook" and Postiv's notebook / whiteboard / book-page styles, the formats people
# picked their tools for; whiteboard and sketchnote pictures were LinkedIn's breakout format of 2026. Both are
# photos of real paper and boards, so they stay inside Voyce's realistic look. A test on 22 Nov 2026 drew a
# five-box sketchnote with every word right in 8 seconds. Each is checked like the Idea picture.
_NB_SURFACES = ("a light oak desk", "a dark walnut desk", "a plain white desk", "a desk with a grey linen cloth")


def _where(company: dict | None) -> str:
    region = _region_for(company)
    return f" in an office in {region}" if region else ""


def _words_check(kind: str, words: list, shows: str) -> str:
    return (f"This photo of {kind} was made for a LinkedIn post. The ONLY words in it should be these, spelled exactly "
            f"(handwriting is fine; capitals may differ): {json.dumps([w for w in words if w], ensure_ascii=False)}.\n"
            f"A misspelt, garbled, doubled or extra word is a blocker, and so are page numbers, margin notes or writing "
            f"on another page.\nWHAT IT SHOULD SHOW: {shows}")


def _explainer_image(content: dict, company: dict | None):
    """The hand-drawn explainer sketch (22 Nov 2026 image-type audit): the post's mechanism in Sketchplanations' style,
    simple ink lines on warm paper, a few hand-lettered labels, one red accent. Teaching visuals earn the most
    reposts (our 111 top posts: 10-20 per 100 reactions); on four real posts this scored 6-8 with the craft judge,
    level with the metaphor Idea picture and above our notebooks. Checked like every drawing, then gated."""
    title = (content.get("ex_title") or "").strip()
    labels = [str(l).strip() for l in content.get("ex_labels") or [] if str(l).strip()][:6]
    sig = _signature_name(company)
    words = ([title] if title else []) + labels + ([sig] if sig else [])
    prompt = ("A hand-drawn explainer sketch in the style of Sketchplanations: simple, confident black ink lines on warm "
              "off-white paper, lots of empty space, flat, no shading, no photo, no frame, no desk. "
              + (f'Hand-lettered title at the top: "{title}". ' if title else "")
              + f"{content.get('ex_scene', '')} Hand-lettered labels, exactly these words and no others: "
              + "; ".join(f'"{w}"' for w in labels)
              + f". The only colour is a red accent on {content.get('ex_accent') or 'the one thing to notice'}. "
              + (f'Signed small at the bottom right in the same hand: "{sig}". ' if sig else "")
              + "Spell every word exactly as given; no other text, no logos. Vertical 4:5, everything large enough to "
                "read on a phone.")
    check = _words_check("a hand-drawn explainer sketch", words,
                         f"a simple ink sketch: {content.get('ex_scene', '')} Red accent on {content.get('ex_accent', '')}.")
    return _checked_image(prompt, check, ratio="4:5", size="1K",
                          compose=lambda pic: _cover_crop(pic, IMG_POST_W, IMG_POST_H), judge_min=JUDGE_MIN["explainer"])


def _notebook_image(content: dict, company: dict | None):
    """The post's framework hand-drawn in a photographed spiral notebook, checked; None when no drawing passes."""
    import random
    title, items, footer = content.get("nb_title", ""), content.get("nb_items") or [], content.get("nb_footer", "")
    lines = content.get("nb_lines") or []
    style = content.get("nb_style") or "sections"
    if style == "sections" and len(lines) >= len(items):
        # The colourful, save-worthy sketchnote people pick Postiv and MagicPost for: highlighted headings in four
        # colours, a small icon each, one short line under each (tested 22 Nov 2026: every word right on Flash)
        sections = "\n".join(f"{i + 1}. '{h.upper()}' - '{ln}'" for i, (h, ln) in enumerate(zip(items, lines)))
        # The author's own colour, not the four-colour rainbow every AI tool now draws (22 Nov 2026 audit)
        hl = _house_highlighter(company)
        page = (f"The right-hand page is filled with a hand-drawn sketchnote in black ink, the headings marked with a "
                f"{hl} highlighter and a small hand-drawn icon beside each section that fits it. Title at the top in "
                f"hand-lettered capitals, underlined twice: '{title.upper()}'. Then {len(items)} numbered sections, each a "
                f"highlighted heading with one short line under it:\n{sections}\n"
                + (f"At the bottom, boxed in the same {hl} ink: '{footer}'. " if footer else ""))
        words = [title] + list(items) + list(lines[:len(items)]) + [footer]
    else:
        accent = _describe_color(_get_palette(company)["accent"]) if _brand_accent(company) else "yellow"
        if style == "checklist":
            body = (f"a checklist of {len(items)} lines, each with a hand-drawn ticked box: "
                    + "; ".join(f"'{x}'" for x in items))
        else:
            body = (f"{len(items)} hand-drawn boxes, one under the other, joined by hand-drawn arrows, numbered: "
                    + "; ".join(f"'{i + 1}. {x}'" for i, x in enumerate(items)))
        page = (f"On the right-hand page, a neat hand-drawn sketchnote in black and dark blue ink with one {accent} "
                f"highlighter accent, titled '{title.upper()}' in hand-lettered capitals. Under the title, {body}. "
                + (f"At the bottom of the page, underlined: '{footer}'. " if footer else ""))
        words = [title] + list(items) + [footer]
    sign = _signature_name(company)
    if sign:
        page += f"Signed small at the bottom right in the same hand: '{sign}'. "
        words = words + [sign]
    prompt = (f"A close-up photo from above of an open spiral notebook lying on {random.choice(_NB_SURFACES)}"
              f"{_where(company)}, soft natural daylight, a plain unbranded pen beside it. {page}"
              "Natural, slightly uneven handwriting with real pen pressure, the way a person writes, never a handwriting "
              "font. Clear and large enough to read on a phone. Every word spelled exactly as given and "
              "nothing else written anywhere: no page numbers, no margin notes, no logos. Shot close: the written page "
              "fills most of the frame, the blank left page only a sliver at the edge. Vertical 4:5.")
    check = _words_check("a notebook sketchnote", words, "a hand-drawn framework on a notebook page: " + "; ".join(items)
                         + ". Section numbers (1, 2, 3...) and small hand-drawn icons are part of the design, not extra words.")
    return _checked_image(prompt, check, ratio="4:5", size="1K",
                          compose=lambda pic: _cover_crop(pic, IMG_POST_W, IMG_POST_H), judge_min=JUDGE_MIN["notebook"])


def _house_highlighter(company: dict | None) -> str:
    """The highlighter colour for an author's handwritten pictures: their brand or house accent."""
    try:
        theme = _pick_card_theme("", company)
        accent = _ed_theme(_card_look(theme, company), _brand_accent(company))["accent"]
        name = _describe_color(accent)
        return name if name not in ("near-black", "dark charcoal", "warm grey", "off-white", "warm cream") else "yellow"
    except Exception:
        return "yellow"


def _signature_name(company: dict | None) -> str:
    """The author's first name for a signature on hand-made pictures ("Mandar"); "" for a company or an odd name."""
    c = company or {}
    if c.get("profile_type") == "company":
        return ""
    first = str(c.get("name") or "").strip().split(" ")[0] if c.get("name") else ""
    return first if first.isalpha() and 2 <= len(first) <= 12 else ""


_OBJECTS = {
    "whiteboard": ("A photo of a whiteboard in a bright modern meeting room{where}, slightly angled, faint traces of "
                   "earlier wiped-off notes, no markers in the frame. Handwritten across the board in dark blue marker in "
                   "natural, slightly uneven handwriting, large and clear: '{line}'. "
                   "{emph}No other writing on the board, no people. The board fills most of the frame.",
                   "The words '{e}' underlined in red marker. "),
    "notebook": ("A close-up photo of an open lined notebook on a desk{where}, warm daylight, a plain unbranded pen "
                 "resting on the page. Handwritten in black ink across the page, large and clear: '{line}'. {emph}No other writing "
                 "anywhere. The page fills most of the frame.",
                 "The words '{e}' marked with a {accent} highlighter. "),
    "book": ("A close-up photo of an open hardcover book, the right-hand page filling most of the frame, warm window "
             "light, shallow depth of field. Printed in the middle of that page in a large classic serif typeface, the "
             "only words on the page: '{line}'. The rest of the page is blank paper with natural texture; no page "
             "numbers, no other text.", ""),
    "sticky": ("A close-up photo of a yellow sticky note stuck on the edge of a plain laptop screen with no logo on "
               "a desk{where}, soft daylight. Handwritten on the note in black marker, large and clear: '{line}'. {emph}No other writing "
               "anywhere, the laptop screen dark. The note fills a third of the frame.",
               "The words '{e}' underlined. "),
}


def _next_object(company: dict | None, line: str) -> str:
    """The object after the one this profile's last line was written on (a sticky note only for short lines)."""
    keys = [k for k in _OBJECTS if k != "sticky" or len(line.split()) <= 8]
    last = str((company or {}).get("object_look") or "")
    return keys[(keys.index(last) + 1) % len(keys)] if last in keys else keys[0]


def _object_quote_image(content: dict, company: dict | None):
    """The post's line written on a real object, checked; None when no drawing passes."""
    line = " ".join(str(content.get("card_headline") or "").split()[:14]).replace("'", "’")
    obj = content.get("object_look") if content.get("object_look") in _OBJECTS else _next_object(company, line)
    tmpl, emph_tmpl = _OBJECTS[obj]
    e = str(content.get("card_emphasis") or "").replace("'", "’").strip()
    emph = emph_tmpl.format(e=e, accent="yellow") if e and emph_tmpl and e.lower() in line.lower() else ""
    prompt = tmpl.format(where=_where(company), line=line, emph=emph) + " Vertical 4:5."
    img = _checked_image(prompt, _words_check(f"a {obj}", [line], f"the line '{line}' written on a {obj}"),
                         ratio="4:5", size="1K", compose=lambda pic: _cover_crop(pic, IMG_POST_W, IMG_POST_H))
    if img is not None:
        try:
            if (company or {}).get("id"):
                import db
                db.companies.update_one({"id": company["id"]}, {"$set": {"object_look": obj}})
                company["object_look"] = obj
        except Exception:
            logging.exception("[Image] could not store the object")
    return img


def render_diagram_png(content: dict, company: dict | None, author: dict | None = None, theme: str = "",
                       meta: dict | None = None) -> bytes | None:
    """A diagram card drawn by Voyce (steps, compare, matrix or stat) in the author's look; None when the writer's
    fields don't make one."""
    import designs
    kind = content.get("diagram_kind")
    brand = (company or {}).get("name", "Voyce")
    name, avatar = _card_author(brand, author)
    title = (company or {}).get("designation", "")
    theme = _pick_card_theme(theme, company)
    t = _v2_look(_card_look(theme, company), company)
    g = lambda k: content.get(k) or ""
    try:
        if kind == "steps" and len(g("dg_items")) >= 3:
            img = designs.steps_card(t, g("dg_title"), g("dg_items"), name, title, avatar)
        elif kind == "compare" and g("dg_left") and g("dg_right"):
            img = designs.compare_card(t, g("dg_title"), g("dg_left_head") or "Most do", g("dg_right_head") or "What works",
                                       g("dg_left"), g("dg_right"), name, title, avatar)
        elif kind == "matrix" and len(g("dg_cells")) == 4:
            img = designs.matrix_card(t, g("dg_title"), g("dg_x"), g("dg_y"), g("dg_cells"), int(content.get("dg_pick") or 0),
                                      name, title, avatar)
        elif kind == "chart" and len(g("dg_bars")) >= 2:
            bars = [(b["label"], b["value"], b["num"]) for b in g("dg_bars")]
            pick = int(content.get("dg_pick") or 0)
            img = designs.chart_card(t, g("dg_title"), bars, pick if 0 <= pick < len(bars) else 0, g("dg_source"),
                                     name, title, avatar)
        elif kind == "spectrum" and g("dg_from") and g("dg_to") and len(g("dg_stops")) >= 2:
            pick = int(content.get("dg_pick") or 0)
            img = designs.spectrum_card(t, g("dg_title"), g("dg_from"), g("dg_to"), g("dg_stops"),
                                        pick if 0 <= pick < len(g("dg_stops")) else len(g("dg_stops")) - 1, name, title, avatar)
        elif kind == "venn" and g("dg_a") and g("dg_b") and g("dg_overlap"):
            img = designs.venn_card(t, g("dg_title"), g("dg_a"), g("dg_b"), g("dg_overlap"), name, title, avatar)
        elif kind == "gap" and g("dg_top") and g("dg_bottom") and g("dg_gap"):
            img = designs.gap_card(t, g("dg_title"), g("dg_top"), g("dg_bottom"), g("dg_gap"), g("dg_x"), name, title, avatar)
        elif kind == "stat" and g("dg_number") and g("dg_line"):
            img = designs.stat_card(t, g("dg_number"), g("dg_line"), g("dg_source"), name, title, avatar, tag=g("dg_title"))
        # round 2 kinds (22 Nov 2026)
        elif kind == "checklist" and len(g("dg_items")) >= 3:
            img = designs.checklist_card(t, g("dg_title"), g("dg_items"), g("dg_line"), name, title, avatar)
        elif kind == "ladder" and len(g("dg_levels")) >= 3 and g("dg_title"):
            img = designs.ladder_card(t["accent"], g("dg_title"), g("dg_levels"), g("dg_from") or "Least",
                                      g("dg_to") or "Most", _signature_name(company), surface=_surface_of(content))
        elif kind == "pairs" and len(g("dg_pairs")) >= 3:
            img = designs.pairs_card(t["accent"], g("dg_pairs_title") or g("dg_title"), g("dg_line"), g("dg_pairs"),
                                     _signature_name(company), surface=_surface_of(content))
        elif kind == "before_after" and g("dg_left") and g("dg_right"):
            before, after = " ".join(g("dg_left")), " ".join(g("dg_right"))   # the writer may split a line into rows
            img = designs.before_after_card(t, g("dg_title"), before, after, g("dg_left_head") or "Before",
                                            g("dg_right_head") or "After", g("dg_before_num"), g("dg_after_num"),
                                            g("dg_num_label"), g("dg_source"), name, title, avatar)
        elif kind == "scorecard" and len(g("dg_items")) >= 3:
            img = designs.scorecard_card(t, g("dg_title"), g("dg_items"), g("dg_bands"), name, title, avatar)
        elif kind == "table" and len(g("dg_cols")) >= 2 and len(g("dg_rows")) >= 2 \
                and len(g("dg_marks")) >= len(g("dg_cols")) * len(g("dg_rows")):
            pick = int(content.get("dg_pick_n", content.get("dg_pick")) or 0)
            img = designs.table_card(t, g("dg_title"), g("dg_cols"), g("dg_rows"), g("dg_marks"),
                                     pick if 0 <= pick < len(g("dg_cols")) else -1, name, title, avatar)
        elif kind == "timeline" and len(g("dg_events")) >= 3:
            pick = int(content.get("dg_pick_n", content.get("dg_pick")) or 0)
            img = designs.timeline_card(t, g("dg_title"), g("dg_events"),
                                        pick if 0 <= pick < len(g("dg_events")) else len(g("dg_events")) - 1, name, title, avatar)
        else:
            return None
    except Exception:
        logging.exception("[Image] diagram failed")
        return None
    if meta is not None:
        meta.update(format="diagram", theme=theme, kind=kind)
        if kind in ("ladder", "pairs"):
            meta["band"] = _BAND_OF_SURFACE[_surface_of(content)]
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _portrait_crop(img: Image.Image) -> Image.Image:
    """The author's photo at 4:5 for the feed, nothing added. A tall photo keeps more of its top (faces sit in the
    upper third), a wide one stays centred."""
    img = img.convert("RGB")
    sw, sh = img.size
    w, h = IMG_POST_W, IMG_POST_H
    scale = max(w / sw, h / sh)
    r = img.resize((max(1, round(sw * scale)), max(1, round(sh * scale))), Image.LANCZOS)
    nw, nh = r.size
    left, top = (nw - w) // 2, int((nh - h) * 0.3)
    return r.crop((left, top, left + w, top + h))


def _cover_crop(img: Image.Image, w: int, h: int) -> Image.Image:
    """Scale + center-crop to exactly w x h (cover fit)."""
    img = img.convert("RGB")
    sw, sh = img.size
    scale = max(w / sw, h / sh)
    resized = img.resize((max(1, round(sw * scale)), max(1, round(sh * scale))), Image.LANCZOS)
    nw, nh = resized.size
    left, top = (nw - w) // 2, (nh - h) // 2
    return resized.crop((left, top, left + w, top + h))


_IMAGE_FORMATS = ("concept", "photo_card", "scene", "quote_card", "list_card", "tweet_card",
                  "notebook", "object_quote", "diagram", "your_photo", "statement_card", "explainer",
                  "note_card", "page_card", "dark_note")
_DIAGRAM_KINDS = ("steps", "compare", "matrix", "stat", "chart", "spectrum", "venn", "gap",
                  "checklist", "before_after", "scorecard", "table", "timeline",   # round 2 (22 Nov 2026)
                  "ladder", "pairs")                                                # hand-lettered (22 Nov 2026)


def _tweet_text(raw, company: dict | None = None) -> str:
    """The post card's words: markdown, hashtags and wrapping quotes gone; list items keep their own lines (a numbered
    mini-story is the strongest card) but other sentences join into paragraphs, because a stack of one-sentence lines
    reads as AI (the first round of post cards came back that way, 22 Nov 2026); at most 45 words (the best cards
    run 14-45); '' when it's too thin to be a post."""
    from generator import _strip_markdown, drop_author_furniture
    text = drop_author_furniture(_strip_markdown(str(raw or "")), company)
    is_item = lambda s: bool(re.match(r"^(\d{1,2}[.)]|[-•→✓✔])\s", s))
    blocks = []
    for blk in re.split(r"\n\s*\n", text.replace("\r", "").strip()):
        lines = [re.sub(r"(?<!\w)#\w+", "", ln).strip().strip('"“”').strip() for ln in blk.split("\n")]
        lines = [ln for ln in lines if ln]
        if not lines:
            continue
        out, para = [], []
        for i, ln in enumerate(lines):
            nxt_item = i + 1 < len(lines) and is_item(lines[i + 1])
            if is_item(ln):
                if para:
                    out.append(" ".join(para)); para = []
                out.append(ln)
            elif nxt_item and not para:
                out.append(ln)            # a lead-in line before a list keeps its own line
            else:
                para.append(ln)
                if nxt_item:
                    out.append(" ".join(para)); para = []
        if para:
            out.append(" ".join(para))
        blocks.append(out)
    # the first sentence lands on its own when the opening block is a paragraph of several
    if blocks and len(blocks[0]) == 1 and not is_item(blocks[0][0]):
        first = re.split(r"(?<=[.!?])\s+", blocks[0][0], maxsplit=1)
        if len(first) == 2 and len(first[0].split()) >= 4:
            blocks = [[first[0]], [first[1]]] + blocks[1:]
    kept, n = [], 0
    for blk in blocks:
        b = []
        for ln in blk:
            w = len(ln.split())
            if n + w > 45:
                break
            b.append(ln)
            n += w
        if b:
            kept.append("\n".join(b))
        if n >= 45 or len(b) < len(blk):
            break
    text = "\n\n".join(kept).strip()
    return text if len(text.split()) >= 6 else ""


def reader_persona(company: dict | None) -> str:
    """Who the author writes for (their prospects), as the profile states it: the analysis's target audience, else
    the rules box's "For ..." line ("1. For CIOs, CTOs and heads of engineering at large enterprises..."). "" when
    neither says; the image writer then infers the reader from the post and the author's world."""
    c = company or {}
    aud = str((c.get("analysis") or {}).get("target_audience") or "").strip()
    if aud:
        return aud[:240]
    kb = c.get("knowledge") or ""
    m = re.search(r"(?im)^\s*(?:\d+[.)]\s*)?(?:written\s+)?for\s+(.{8,240}?)\s*$", kb)
    if m:
        return m.group(1).rstrip(".")
    m = re.search(r"(?im)^\s*(?:audience|readers?|ideal clients?|who (?:i|they) write for)\s*[:\-]\s*(.{8,240}?)\s*$", kb)
    return m.group(1).rstrip(".") if m else ""


def _number_in(num: str, text: str) -> bool:
    """Whether the number on a stat card ("77%", "$1.5B", "3x") is one the text states."""
    core = re.sub(r"[,\s]", "", str(num or ""))
    core = re.search(r"\d+(?:\.\d+)?", core)
    if not core:
        return False
    stated = {re.sub(r",", "", m) for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text or "")}
    return core.group(0) in stated


def _timeline_events(events, text: str) -> list:
    """The career timeline's milestones: kept only when their year or date is one the post or the author's profile
    states ("Today" / "Now" may close it); at most 6, each event max 8 words. A made-up year never reaches a card."""
    out = []
    for e in events or []:
        if not isinstance(e, dict):
            continue
        when = " ".join(str(e.get("when") or "").split()[:3])
        what = " ".join(str(e.get("what") or "").split()[:8])
        if not when or not what:
            continue
        if when.lower() in ("today", "now", "this year") or (re.search(r"\d", when) and _number_in(when, text)):
            out.append({"when": when[:14], "what": what})
    return out[:6]


def _chart_bars(bars, text: str) -> list:
    """A chart's bars, only those whose value the post states: [{"label", "value", "num"}]."""
    out = []
    for b in bars or []:
        if not isinstance(b, dict):
            continue
        label = " ".join(str(b.get("label") or "").split()[:5])
        value = str(b.get("value") or "").strip()[:12]
        m = re.search(r"\d+(?:[.,]\d+)?", value.replace(",", ""))
        if label and m and _number_in(value, text):
            out.append({"label": label, "value": value, "num": float(m.group(0))})
    return out[:6]


def _pairs(xs, n: int = 6, words: int = 4) -> list:
    """Up to n (when, do) pairs, both sides non-empty and short ('OVERTHINKING -> WRITE'); a side over `words` words
    drops the pair rather than cutting it mid-phrase."""
    out = []
    for x in xs or []:
        if isinstance(x, dict):
            a, b = " ".join(str(x.get("when") or "").split()), " ".join(str(x.get("do") or "").split())
            if a and b and len(a.split()) <= words and len(b.split()) <= words:
                out.append((a, b))
    return out[:n]


def _short_list(xs, n: int, words: int) -> list:
    """Up to n non-empty strings, each cut to its first `words` words."""
    out = []
    for x in xs or []:
        s = " ".join(str(x).split()[:words]).strip()
        if s:
            out.append(s)
    return out[:n]


# ── variety: the background of each picture (22 Nov 2026 variety research) ──────────────────────────────────────
# Keep the author's identity (hand, ink, accent, signature), change the form: never two paper pictures in a row and
# at least 3 of the backgrounds in any 5 posts. A picture's background ("band"): photo, paper, light, dark, art.
_BAND_OF_SURFACE = {"paper": "paper", "white": "light", "dark": "dark"}
_SURFACE_OF_BAND = {"paper": "paper", "light": "white", "dark": "dark"}
VARY_BACKGROUNDS = os.getenv("VARY_BACKGROUNDS", "1") != "0"


def _surface_of(content: dict) -> str:
    s = str((content or {}).get("surface") or "paper")
    return s if s in _BAND_OF_SURFACE else "paper"


def _least_used(recent: list, allowed: tuple) -> str:
    """The band in allowed the last few pictures used least, never the last one's when another is allowed; ties go
    to allowed's order (its first is the default)."""
    last = recent[0] if recent else ""
    choices = [b for b in allowed if b != last] or list(allowed)
    return min(choices, key=lambda b: (recent[:4].count(b), allowed.index(b)))


def picture_band(meta: dict, content: dict, company: dict | None) -> str:
    """What a drawn picture's background reads as, for the next one to differ."""
    if meta.get("band"):
        return meta["band"]
    fmt = meta.get("format") or content.get("format") or ""
    if fmt == "your_photo":
        return "photo"
    if fmt in ("explainer", "notebook", "page_card", "note_card"):
        return "paper"
    if fmt in ("concept", "photo_card", "scene", "object_quote"):
        return "art"
    try:
        look = _v2_look(_card_look(meta.get("theme") or "", company), company)
        return "dark" if _luminance(look["bg"]) < 0.4 else "light"
    except Exception:
        return "light"


def _vary(out: dict, company: dict | None) -> dict:
    """The hand-made cards and notes take the background the author's last pictures used least; a note or a page
    right after a paper picture becomes the dark typed note."""
    recent = [b for b in (company or {}).get("recent_bands") or [] if b]
    if not VARY_BACKGROUNDS or not recent:
        return out
    f, k = out["format"], out["diagram_kind"]
    if f == "diagram" and k in ("ladder", "pairs"):
        out["surface"] = _SURFACE_OF_BAND[_least_used(recent, ("paper", "light", "dark"))]
    elif f in ("note_card", "page_card") and recent[0] == "paper" and (out["card_headline"] or out["tweet_text"]):
        out["format"] = "dark_note"
    return out


def generate_ai_image_post(raw_text: str, company: dict = None, force_format: str = None,
                           has_photos: bool | None = None) -> dict:
    """LLM: pick the strongest idea → a concrete visual concept + caption for the post image.
    force_format pins the output to one of _IMAGE_FORMATS when the user chose the format
    explicitly instead of letting the model pick. has_photos: whether the author has uploaded photos
    of themselves (looked up when not given), so "your_photo" is only chosen when one can be drawn."""
    from generator import _strip_markdown, drop_author_furniture
    if has_photos is None:
        try:
            import author_photos
            has_photos = author_photos.count((company or {}).get("id", "")) > 0
        except Exception:
            has_photos = False
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
    reader = reader_persona(company)
    world += (f"\nTHE READER (the author's prospects): {reader}." if reader else
              "\nTHE READER: not stated; infer who this author's prospects are from the post and the author's world.")
    world += ("\nMake the picture for THE READER: objects, documents, labels and rooms from their working day (what is "
              "on their desk, in their board pack, on their meeting table), so they recognise their own world at a glance, "
              "and a headline that speaks to them in their words. Never their company's or anyone's logo.")
    prompt = f"""Content to turn into ONE post image + caption:

{raw_text[:_FROM_TEXT_MAX]}{voice_section}{world}

Choose the single strongest idea and design the visual for it.

RELEVANCE GATE: before answering, check your concept against the post's core claim. A reader
scrolling past must connect the image to the post's point INSTANTLY, with zero explanation.
If the connection needs a caption to make sense, discard the concept and pick a clearer one.

Also return "key_line": the single most quotable line from the post itself (max 12 words,
verbatim or lightly trimmed) — punchy enough to stand alone on a typographic card.
AUTHOR HAS PHOTOS OF THEMSELVES: {"yes" if has_photos else "no (never pick your_photo)"}
{f'''
REQUIRED FORMAT: "{force_format}" — the user chose this format explicitly. Design for exactly
this format and set "format" to this value.''' if force_format in _IMAGE_FORMATS else ""}
Return ONLY valid JSON (fill only the fields your format uses; leave the rest empty):
{{"reader": "who the picture is for, in a few words", "reader_artifact": "(concept) the reader's own thing the picture is built on", "post_shape": "list|contrast|tradeoff|stat|claim|argument|moment", "format": "{'|'.join(_IMAGE_FORMATS)}",
"image_concept": "... (concept, photo_card and scene)", "image_words": ["... (concept)"],
"card_tag": "...", "card_headline": "...", "card_emphasis": "...", "card_subtext": "...", "tweet_text": "... (tweet_card)",
"page_setup": "... (page_card)",
"ex_title": "... (explainer)", "ex_scene": "...", "ex_labels": ["..."], "ex_accent": "...",
"card_points": ["...", "..."], "card_icons": ["...", "..."],
"nb_title": "...", "nb_items": ["..."], "nb_lines": ["..."], "nb_style": "sections|flow|checklist", "nb_footer": "...",
"diagram_kind": "ladder|pairs|steps|compare|matrix|stat|chart|spectrum|venn|gap|checklist|before_after|scorecard|table|timeline", "dg_title": "...", "dg_items": ["..."],
"dg_levels": ["... (ladder, worst first)"], "dg_pairs_title": "...", "dg_pairs": [{{"when": "...", "do": "..."}}], "dg_left_head": "...",
"dg_right_head": "...", "dg_left": ["..."], "dg_right": ["..."], "dg_x": "...", "dg_y": "...", "dg_cells": ["...", "...", "...", "..."],
"dg_pick": 0, "dg_number": "...", "dg_line": "...", "dg_source": "...", "dg_bars": [{{"label": "...", "value": "..."}}],
"dg_from": "...", "dg_to": "...", "dg_stops": ["..."], "dg_a": "...", "dg_b": "...", "dg_overlap": "...",
"dg_top": "...", "dg_bottom": "...", "dg_gap": "...", "dg_before_num": "...", "dg_after_num": "...", "dg_num_label": "...",
"dg_bands": ["..."], "dg_cols": ["..."], "dg_rows": ["..."], "dg_marks": ["..."], "dg_events": [{{"when": "...", "what": "..."}}],
"alt_text": "...", "key_line": "...", "post_text": "..."}}"""
    result = generate_json(prompt, system=_AI_IMAGE_SYSTEM, max_tokens=2200, temperature=0.85)

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
        fmt = _LINE_CARD if (result.get("card_headline") or "").strip() else "quote_card"
    if fmt == "your_photo" and not has_photos:
        fmt = _LINE_CARD if (result.get("card_headline") or "").strip() else "quote_card"
    # Retired from Auto (22 Nov 2026 image audit, both in the craft judge's generic band, 4-5): the AI stock-like
    # photo and the whiteboard quote. Still made when asked for by name.
    # And (22 Nov 2026 opinion-visual research) the tweet-screenshot card, the weakest single-line picture, and the
    # designed statement card: a line goes on the handwritten note instead.
    retired = ("photo_card", "scene", "object_quote") + (("tweet_card", "statement_card") if NOTE_CARD else ())
    if force_format not in _IMAGE_FORMATS and fmt in retired:
        fmt = _LINE_CARD if (result.get("card_headline") or result.get("key_line") or "").strip() else "quote_card"
    g = lambda k: str(result.get(k) or "").strip()
    try:
        pick = int(result.get("dg_pick") or 0)
    except (TypeError, ValueError):
        pick = 0
    kind = g("diagram_kind").lower()
    if kind == "stat" and not _number_in(g("dg_number"), raw_text):
        kind = ""   # a number the post doesn't state never goes on a card; the renderer falls back
    out = {
        "format":        fmt,
        "post_shape":    g("post_shape").lower(),
        "reader":        g("reader")[:120],
        "reader_artifact": g("reader_artifact")[:120],
        "nb_title":      " ".join(g("nb_title").split()[:8]),
        "nb_items":      _short_list(result.get("nb_items"), 5, 6),
        "nb_lines":      _short_list(result.get("nb_lines"), 5, 9),
        "nb_style":      g("nb_style").lower() if g("nb_style").lower() in ("sections", "flow", "checklist") else "sections",
        "nb_footer":     " ".join(g("nb_footer").split()[:10]),
        "diagram_kind":  kind if kind in _DIAGRAM_KINDS else "",
        "dg_title":      " ".join(g("dg_title").split()[:10]),
        "dg_items":      _short_list(result.get("dg_items"), 7, 10),
        "dg_left_head":  " ".join(g("dg_left_head").split()[:4]),
        "dg_right_head": " ".join(g("dg_right_head").split()[:4]),
        "dg_left":       _short_list(result.get("dg_left"), 4, 8),
        "dg_right":      _short_list(result.get("dg_right"), 4, 8),
        "dg_x":          " ".join(g("dg_x").split()[:6]),
        "dg_y":          " ".join(g("dg_y").split()[:6]),
        "dg_cells":      _short_list(result.get("dg_cells"), 4, 6),
        "dg_pick":       pick if 0 <= pick <= 3 else 0,
        "dg_number":     g("dg_number")[:12],
        "dg_line":       " ".join(g("dg_line").split()[:16]),
        "dg_source":     g("dg_source")[:60],
        "dg_bars":       _chart_bars(result.get("dg_bars"), raw_text),
        "dg_from":       " ".join(g("dg_from").split()[:4]),
        "dg_to":         " ".join(g("dg_to").split()[:4]),
        "dg_stops":      _short_list(result.get("dg_stops"), 5, 5),
        "dg_a":          " ".join(g("dg_a").split()[:5]),
        "dg_b":          " ".join(g("dg_b").split()[:5]),
        "dg_overlap":    " ".join(g("dg_overlap").split()[:7]),
        "dg_top":        " ".join(g("dg_top").split()[:5]),
        "dg_bottom":     " ".join(g("dg_bottom").split()[:5]),
        "dg_gap":        " ".join(g("dg_gap").split()[:5]),
        # round 2 kinds: a result number or a year only when the post (or the author's profile) states it
        "dg_before_num": g("dg_before_num")[:10] if _number_in(g("dg_before_num"), raw_text) else "",
        "dg_after_num":  g("dg_after_num")[:10] if _number_in(g("dg_after_num"), raw_text) else "",
        "dg_num_label":  " ".join(g("dg_num_label").split()[:4]),
        "dg_bands":      _short_list(result.get("dg_bands"), 4, 9),
        "dg_cols":       _short_list(result.get("dg_cols"), 4, 3),
        "dg_rows":       _short_list(result.get("dg_rows"), 6, 5),
        "dg_marks":      _short_list(result.get("dg_marks"), 24, 2),
        "dg_events":     _timeline_events(result.get("dg_events"), raw_text),
        "dg_levels":     _short_list(result.get("dg_levels"), 5, 14),
        "dg_pairs":      _pairs(result.get("dg_pairs")),
        "dg_pairs_title": " ".join(g("dg_pairs_title").split()[:6]),
        "dg_pick_n":     pick if 0 <= pick <= 9 else 0,
        "ex_title":      " ".join(g("ex_title").split()[:7]),
        "ex_scene":      " ".join(g("ex_scene").split()[:90]),
        "ex_labels":     _short_list(result.get("ex_labels"), 6, 5),
        "ex_accent":     " ".join(g("ex_accent").split()[:12]),
        "image_concept": (result.get("image_concept") or "").strip(),
        "image_words":   [str(x).strip() for x in (result.get("image_words") or []) if str(x).strip()][:12],
        "card_tag":      drop_author_furniture((result.get("card_tag") or "").strip(), company),
        "card_headline": (result.get("card_headline") or "").strip(),
        "card_emphasis": (result.get("card_emphasis") or "").strip(),
        "card_subtext":  (result.get("card_subtext") or "").strip(),
        "tweet_text":    _tweet_text(result.get("tweet_text"), company),
        "page_setup":    " ".join(g("page_setup").split()[:70]),
        "card_points":   [str(x).strip() for x in (result.get("card_points") or []) if str(x).strip()][:4],
        "card_icons":    [(str(x).strip().lower() if str(x).strip().lower() in CARD_ICONS else "")
                          for x in (result.get("card_icons") or [])][:4],
        "alt_text":      (result.get("alt_text") or "").strip(),
        "key_line":      (result.get("key_line") or "").strip(),
        "post_text":     drop_author_furniture(_strip_markdown(result.get("post_text", "") or ""), company),
    }
    # The plain checklist is the weakest list picture (22 Nov 2026: the writer still chose it for 3 of 4 list posts):
    # the same checks go on the ladder, else as pairs. Kept only when neither was written.
    if out["format"] == "diagram" and out["diagram_kind"] == "checklist":
        if len(out["dg_levels"]) >= 3 and out["dg_title"]:
            out["diagram_kind"] = "ladder"
        elif len(out["dg_pairs"]) >= 3:
            out["diagram_kind"] = "pairs"
    # The writer names the post's shape first; a format that doesn't fit it is put right when the fields for the
    # right one are there (22 Nov 2026: Auto drew an Idea picture for a ✔-checklist post)
    if force_format not in _IMAGE_FORMATS:
        shape, f = out["post_shape"], out["format"]
        if shape == "list" and f not in ("notebook", "diagram"):
            if len(out["dg_levels"]) >= 3:
                out.update(format="diagram", diagram_kind="ladder")
            elif len(out["dg_pairs"]) >= 3:
                out.update(format="diagram", diagram_kind="pairs")
            elif len(out["nb_items"]) >= 3:
                out["format"] = "notebook"
            elif len(out["dg_items"]) >= 3:
                out.update(format="diagram", diagram_kind="steps")
        elif shape in ("contrast", "tradeoff", "stat", "numbers", "range", "overlap", "gap", "self_check", "options",
                       "journey") and f != "diagram":
            kind = {"contrast": "compare", "tradeoff": "matrix", "stat": "stat", "numbers": "chart", "range": "spectrum",
                    "overlap": "venn", "gap": "gap", "self_check": "scorecard", "options": "table", "journey": "timeline"}[shape]
            ready = {"compare": out["dg_left"] and out["dg_right"], "matrix": len(out["dg_cells"]) == 4,
                     "scorecard": len(out["dg_items"]) >= 3,
                     "table": len(out["dg_cols"]) >= 2 and len(out["dg_rows"]) >= 2
                              and len(out["dg_marks"]) >= len(out["dg_cols"]) * len(out["dg_rows"]),
                     "timeline": len(out["dg_events"]) >= 3,
                     "stat": out["dg_number"] and out["dg_line"] and _number_in(out["dg_number"], raw_text),
                     "chart": len(out["dg_bars"]) >= 2, "spectrum": out["dg_from"] and out["dg_to"] and len(out["dg_stops"]) >= 2,
                     "venn": out["dg_a"] and out["dg_b"] and out["dg_overlap"],
                     "gap": out["dg_top"] and out["dg_bottom"] and out["dg_gap"]}[kind]
            if ready:
                out.update(format="diagram", diagram_kind=kind)
        elif shape == "mechanism" and f != "explainer" and out["ex_scene"] and len(out["ex_labels"]) >= 2:
            out["format"] = "explainer"
        elif shape == "claim" and f not in ("tweet_card", "statement_card", "note_card", "page_card") and out["card_headline"]:
            out["format"] = _LINE_CARD
        # Never the same kind of picture twice in a row for one author (22 Nov 2026: with list posts common, four
        # Auto pictures in a row came out as notebooks): a list alternates notebook and Voyce's steps diagram, a
        # claim alternates handwritten quote and statement card.
        last = str((company or {}).get("last_picture_format") or "")
        last_kind = str((company or {}).get("last_picture_kind") or "")
        f, k = out["format"], out["diagram_kind"]
        if f == "diagram" and k in ("ladder", "pairs") and last == "diagram" and last_kind == k:
            # the other hand-lettered structure, else the notebook
            other = "pairs" if k == "ladder" else "ladder"
            if len(out["dg_pairs" if other == "pairs" else "dg_levels"]) >= 3:
                out["diagram_kind"] = other
            elif len(out["nb_items"]) >= 3:
                out["format"] = "notebook"
        elif f == "notebook" and last == "notebook" and (len(out["dg_levels"]) >= 3 or len(out["dg_pairs"]) >= 3):
            out.update(format="diagram", diagram_kind="ladder" if len(out["dg_levels"]) >= 3 else "pairs")
        elif f == "notebook" and last == "notebook" and len(out["nb_items"]) >= 3:
            out.update(format="diagram", diagram_kind="steps", dg_title=out["dg_title"] or out["nb_title"],
                       dg_items=out["dg_items"] or out["nb_items"])
        elif f == "diagram" and out["diagram_kind"] in ("steps", "checklist") and last == "diagram" and len(out["nb_items"] or out["dg_items"]) >= 3:
            out.update(format="notebook", nb_title=out["nb_title"] or out["dg_title"],
                       nb_items=out["nb_items"] or out["dg_items"][:5])
        elif f == "object_quote" and last == "object_quote":
            out["format"] = "tweet_card"
        elif f == "tweet_card" and last == "tweet_card" and out["card_headline"]:
            out["format"] = "statement_card"
        elif f == "explainer" and last == "explainer" and (out["dg_left"] and out["dg_right"] or len(out["dg_items"]) >= 3):
            out.update(format="diagram", diagram_kind="compare" if out["dg_left"] and out["dg_right"] else "steps")
        elif f == "statement_card" and last == "statement_card":
            out["format"] = "tweet_card" if out["tweet_text"] else "quote_card"
        elif f == "note_card" and last == "note_card":
            out["format"] = "page_card" if out["page_setup"] else "statement_card"
        elif f == "page_card" and last == "page_card":
            out["format"] = "note_card"
    if force_format not in _IMAGE_FORMATS:
        out = _vary(out, company)
    if out["format"] == "concept" and out["image_concept"] and out["card_headline"]:
        out.update(concept_metaphor(out, company))
    return out


# ── metaphor-first Idea pictures (22 Nov 2026 audit, round 2) ─────────────────────────────────────
# The illustration research: Buffer gave nine image models one realistic still-life prompt and got the same picture
# back from all nine, the family our literal "reader's artifact with a red circle" belongs to. The best editorial
# pictures FUSE two things into one idea (a container ship stuck in a bottle for "bottleneck"): a real object with
# one impossible change. On our model, six posts: the fused metaphor won three times by a wide margin (a janitor's
# key ring tagged LOAN FILES for an over-permissioned agent, 9 vs 7.5) and lost when the change was subtle (fading,
# fraying). So: a separate art-director step writes a BOLD metaphor, tried first; the reader's-artifact picture the
# writer drew up stays as the fallback, so a metaphor that fails the check costs a retry, never the picture.
CONCEPT_METAPHOR = os.getenv("CONCEPT_METAPHOR", "1") != "0"

_METAPHOR_SYSTEM = """You are the art director of a top business magazine (The Economist covers, HBR, Bloomberg
Businessweek). You turn one LinkedIn post into ONE picture idea that a stranger gets in two seconds on a phone."""


def _metaphor_brief(content: dict) -> str:
    return f"""THE POST:
{(content.get('post_text') or '')[:1500]}

HEADLINE SET ABOVE THE PICTURE (we set it; never draw it): {content.get('card_headline', '')}
THE READER: {content.get('reader') or "the post's reader"}
THE READER'S OWN ARTIFACT (an object from their working day): {content.get('reader_artifact', '')}

Work in this order:
1. thesis: the post's one point in under 12 words.
2. Three candidate VISUAL FUSIONS, each "X that is also Y": one real, photographable object from THE READER's world
   with ONE impossible change that IS the post's tension. The change must be BOLD and visible at thumbnail size: a
   change of scale (a key ring with fifty keys and one small tag), a cut or tear, two things joined or laced together,
   one object made out of another, something locked, stitched, overflowing or snapped. Never a subtle change: fading,
   fraying, thinning, blur, a row of near-identical objects, or anything that needs a close look.
3. Judge each: would it fit someone else's post on this topic (then reject it)? Does it read in two seconds with one
   hero object and at most one supporting object? Can an image model draw it cleanly?
4. Write up the best as concept: 2-3 sentences for a still-life photographer, the fused object(s) only, how they sit,
   what the eye goes to. Real materials. words: usually none: the best metaphor pictures (a table dipping under a
   ball, sand poured into an hourglass from outside) carry no words at all, and the headline above already says it.
   At most 2 short words ON the object, only when the idea can't read without them.
Never: lightbulbs, handshakes, rockets, chess pieces, gears, puzzle pieces, upward arrows, ladders, targets, mountains,
globes, robots, brains, circuits, screens with apps, coffee cups, decorative plants, desks or tables, people, faces.
Numbers only from the post.

Return JSON: {{"thesis": "...", "candidates": ["...", "...", "..."], "pick": 0, "concept": "...", "words": ["..."]}}"""


def concept_metaphor(content: dict, company: dict | None = None) -> dict:
    """The bold metaphor for an Idea picture ({metaphor_concept, metaphor_words}), or {} when it's off or fails."""
    if not CONCEPT_METAPHOR:
        return {}
    try:
        r = generate_json(_metaphor_brief(content), system=_METAPHOR_SYSTEM, max_tokens=1800, temperature=0.9)
    except Exception as e:
        logging.info(f"[Image] metaphor step skipped: {e}")
        return {}
    concept = str(r.get("concept") or "").strip()
    words = [str(w).strip() for w in (r.get("words") or []) if str(w).strip()][:2]
    banned = re.compile(r"\b(light ?bulb|handshake|rocket|chess|gear|puzzle|robot|brain|circuit|ladder|target|"
                        r"mountain|globe|coffee|desk|table|face|person|people)s?\b", re.I)
    if len(concept) < 40 or banned.search(concept):
        return {}
    return {"metaphor_concept": concept, "metaphor_words": words}


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
    """The image for a post, and for the designed cards (list, quote, tweet) up to 3 colour versions
    of it to choose from. Those are drawn here, so the extra versions cost nothing; concept pictures
    and photos come one at a time. [{"png": bytes, "format": ..., "theme": ...}], the asked-for one
    first."""
    fmt = (content.get("format") or "scene").strip().lower()
    n = max(1, min(3, int(n or 1)))
    photo = None
    if fmt == "photo_card":   # one photo, however many colour versions of its panel
        photo = _ai_photo(content, company)
        if photo is None:
            n = 1             # it falls back to a card below
    elif fmt == "your_photo":   # one of their photos, the same in every colour version
        try:
            import author_photos
            photo = author_photos.pick(company)
        except Exception:
            photo = None
    designed = ((fmt == "list_card" and content.get("card_points"))
                or (fmt in ("quote_card", "tweet_card", "statement_card") and (content.get("card_headline") or content.get("key_line")))
                or (fmt == "photo_card" and photo is not None)
                or (fmt == "your_photo" and photo is not None and YOUR_PHOTO_LINE)
                or (fmt == "diagram" and content.get("diagram_kind")))
    out = []
    themes = _option_themes(theme, n, company) if designed else [theme]
    if fmt == "tweet_card" and TWEET_CARD_STYLE == "post":   # a post card comes in two looks: the house one first
        first = _post_mode(theme or _pick_card_theme("", company), company)
        themes = [first, "light" if first == "dark" else "dark"][:n]
    for th in themes:
        meta: dict = {}
        png = render_ai_image_png(content, company, author=author, theme=th, meta=meta,
                                  photo=(photo if photo is not None else False) if fmt in ("photo_card", "your_photo") else None)
        out.append({"png": png, "format": meta.get("format", fmt), "theme": meta.get("theme", "")})
    return out


def _ai_photo(content: dict, company: dict, ratio: str = "5:4"):
    """The realistic photo for a post's concept, checked, or None when the image model fails.
    5:4 for the photo card's top part, 4:5 for a photo on its own."""
    concept = (content.get("image_concept") or "").strip()
    if not concept:
        return None
    check = (f"This photo was made for a LinkedIn post. WHAT IT SHOULD SHOW: {concept}\n"
             "It must carry no readable words at all. People's faces are allowed.")
    return _checked_image(_build_ai_image_prompt(concept, _get_palette(company), _region_for(company), ratio),
                          check, ratio=ratio, size="1K")


def render_ai_image_png(content: dict, company: dict, author: dict | None = None,
                        theme: str = "", meta: dict | None = None, photo=None) -> bytes:
    """The post's image at 4:5: a concept picture, a photo (with or without its line) or one of
    the designed cards. Falls back to the branded insight card if image generation is unavailable.
    theme picks the colour set (else the author's brand look, else random); meta, when given, gets
    what was drawn (format, colour set)."""
    meta = {} if meta is None else meta
    p = _get_palette(company)
    brand = (company or {}).get("name", "Voyce")
    concept = (content.get("image_concept") or "").strip()
    fmt = (content.get("format") or "scene").strip().lower()

    def _png(img) -> bytes:
        buf = io.BytesIO()
        _cover_crop(img, IMG_POST_W, IMG_POST_H).save(buf, format="PNG")
        return buf.getvalue()

    if fmt == "notebook" and len(content.get("nb_items") or []) >= 2:
        img = _notebook_image(content, company)
        if img is not None:
            meta.update(format="notebook", theme="")
            return _png(img)
        # the drawing didn't pass: the same framework as steps Voyce draws, every word exact
        content = {**content, "format": "diagram", "diagram_kind": "steps",
                   "dg_title": content.get("nb_title", ""), "dg_items": content.get("nb_items") or []}
        fmt = "diagram"
    if fmt == "object_quote" and content.get("card_headline"):
        img = _object_quote_image(content, company)
        if img is not None:
            meta.update(format="object_quote", theme="")
            return _png(img)
        fmt = "quote_card"   # the line on a designed card
    if fmt == "explainer" and content.get("ex_scene") and len(content.get("ex_labels") or []) >= 2:
        img = _explainer_image(content, company)
        if img is not None:
            meta.update(format="explainer", theme="")
            return _png(img)
        # the sketch didn't pass (words or the craft gate): the same point on an exact card Voyce draws
        if content.get("dg_left") and content.get("dg_right"):
            content, fmt = {**content, "format": "diagram", "diagram_kind": "compare"}, "diagram"
        elif len(content.get("dg_items") or []) >= 3:
            content, fmt = {**content, "format": "diagram", "diagram_kind": "steps"}, "diagram"
        else:
            fmt = _LINE_CARD if content.get("card_headline") else "quote_card"
    if fmt == "diagram":
        png = render_diagram_png(content, company, author=author, theme=theme, meta=meta)
        if png:
            return png
        items = content.get("dg_items") or content.get("nb_items") or content.get("dg_right") or []
        if items:
            content = {**content, "card_points": items, "card_headline": content.get("dg_title") or content.get("key_line", "")}
            fmt = "list_card"
        else:
            fmt = "quote_card"
    if fmt == "your_photo" and (content.get("card_headline") or not YOUR_PHOTO_LINE):
        if photo is None:
            try:
                import author_photos
                photo = author_photos.pick(company)
            except Exception:
                logging.exception("[Image] could not read the author's photos")
                photo = None
        if photo is not None and photo is not False and not (YOUR_PHOTO_LINE and content.get("card_headline")):
            meta.update(format="your_photo", theme="")
            return _png(_portrait_crop(photo))           # the photo as it is: the caption tells the story
        if photo is not None and photo is not False:
            name, avatar_img = _card_author(brand, author)
            theme = _pick_card_theme(theme, company)
            meta.update(format="your_photo", theme=theme)
            img = None
            if min(photo.size) >= 700:   # a sharp photo fills the frame, the line on a floating card (Postiv's way)
                try:
                    import designs
                    img = designs.photo_float_card(_v2_look(_card_look(theme, company), company), photo,
                                                   content["card_headline"], content.get("card_emphasis", ""), name,
                                                   (company or {}).get("designation", ""))
                except Exception:
                    logging.exception("[Image] floating photo card failed; drawing the band")
            if img is None:   # a small photo would blur at full size: photo above, the line on a band
                # key words in the accent colour, never a highlighter box behind them (Rishav, 1 Nov 2026)
                look = {**_ed_theme(_card_look(theme, company), _brand_accent(company)), "mark": "text"}
                img = _ed_photo_card(photo, content["card_headline"], content.get("card_emphasis", ""), name,
                                     (company or {}).get("designation", ""), avatar_img, theme=look,
                                     rw=IMG_POST_W * _SCALE, rh=IMG_POST_H * _SCALE)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            return buf.getvalue()
        fmt = "quote_card"

    if fmt == "concept" and concept and content.get("card_headline"):
        theme = _pick_card_theme(theme, company)
        site = str(((company or {}).get("brand_kit") or {}).get("site") or "").strip()[:40]
        # The turn in colour, no highlighter: a mark across a picture's headline reads as a sticker.
        # Two sets have a dark accent next to near-black words; they take a warmer one.
        look = {**_ed_theme(_card_look(theme, company), _brand_accent(company)), "mark": "text"}
        if theme in _CONCEPT_TURN and not _brand_accent(company):
            look["accent"] = _CONCEPT_TURN[theme]
        img = _concept_image(content, company, look, site or _card_author(brand, author)[0])
        if img is not None:
            meta.update(format="concept", theme=theme)
            buf = io.BytesIO()
            _cover_crop(img, IMG_POST_W, IMG_POST_H).save(buf, format="PNG")
            return buf.getvalue()
        # the picture didn't come: its headline, on a card
        card = {"card_headline": content["card_headline"], "card_emphasis": content.get("card_emphasis", ""),
                "card_subtext": "", "card_tag": ""}
        return render_image_post_png(card, company, author=author, theme=theme, meta=meta)

    # Designed value cards: information over decoration. Rendered by us — always crisp, always relevant.
    if fmt == "list_card" and content.get("card_points"):
        name, avatar_img = _card_author(brand, author)
        theme = _pick_card_theme(theme, company)
        meta.update(format="list_card", theme=theme)
        img = None
        if CARD_DESIGN == "v2":
            try:
                import designs
                img = designs.list_card(_v2_look(_card_look(theme, company), company), content.get("card_tag", ""),
                                        content.get("card_headline") or content.get("key_line", ""),
                                        content.get("card_emphasis", ""), designs.points_from(content.get("card_points")),
                                        content.get("card_icons") or [], name, (company or {}).get("designation", ""),
                                        avatar_img)
            except Exception:
                logging.exception("[Carousel] list card v2 failed; drawing the editorial one")
        img = img or _ed_list_card(
            content.get("card_tag", ""), content.get("card_headline") or content.get("key_line", ""),
            content.get("card_emphasis", ""), content.get("card_points"), name,
            (company or {}).get("designation", ""), avatar_img,
            theme=_card_look(theme, company), brand_accent=_brand_accent(company),
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
        if TWEET_CARD_STYLE == "post" and (content.get("tweet_text") or "").strip():
            card_line = content["tweet_text"].strip()   # the post-shaped text, line breaks kept
        return render_statement_card_png(card_line, company, author=author, theme=theme, meta=meta)
    if fmt == "page_card" and not (content.get("page_setup") or "").strip():
        fmt = "note_card"                                 # no setup written: the line on its own
    if fmt == "dark_note" and (content.get("tweet_text") or content.get("page_setup") or content.get("card_headline")
                               or content.get("key_line")):
        try:
            import designs
            accent = _v2_look(_card_look(_pick_card_theme(theme, company), company), company)["accent"]
            text = (content.get("tweet_text") or "").strip() or " ".join(
                x for x in ((content.get("page_setup") or "").strip(),
                            (content.get("card_headline") or content.get("key_line") or "").strip()) if x)
            img = designs.typed_note(accent, " ".join(text.split()[:120]) if "\n\n" not in text else text,
                                     _signature_name(company), "dark")
            meta.update(format="dark_note", theme="", band="dark")
            return _png(img)
        except Exception:
            logging.exception("[Image] dark note failed; drawing the note")
            fmt = "note_card"
    if fmt in ("note_card", "page_card") and (content.get("card_headline") or content.get("key_line")):
        try:
            import designs
            accent = _v2_look(_card_look(_pick_card_theme(theme, company), company), company)["accent"]
            line = (content.get("card_headline") or content.get("key_line", "")).strip()
            if fmt == "note_card":
                img = designs.note_card(accent, " ".join(line.split()[:30]), content.get("card_emphasis", ""),
                                        _signature_name(company), surface=_surface_of(content))
            else:
                img = designs.page_card(accent, content["page_setup"], " ".join(line.split()[:16]),
                                        (company or {}).get("name", "") if (company or {}).get("profile_type") != "company" else "")
            meta.update(format=fmt, theme="", band=_BAND_OF_SURFACE[_surface_of(content)] if fmt == "note_card" else "paper")
            return _png(img)
        except Exception:
            logging.exception("[Image] %s failed; drawing the statement card", fmt)
            fmt = "statement_card"
    if fmt == "statement_card" and (content.get("card_headline") or content.get("key_line")):
        # One big sentence on a plain card (top-posts research, 22 Nov 2026: Steven Bartlett's 11,146-reaction card),
        # its own format again beside the post-style tweet card
        line = (content.get("card_headline") or content.get("key_line", "")).strip()
        png = render_statement_card_png(line, company, author=author, theme=theme, meta=meta, style="statement")
        if meta is not None:
            meta["format"] = "statement_card"
        return png
    if fmt == "photo_card":
        # photo: the one already made, False when it was tried and failed, None to make it here
        photo = _ai_photo(content, company) if photo is None else photo
        if photo is not False and photo is not None:
            name, avatar_img = _card_author(brand, author)
            theme = _pick_card_theme(theme, company)
            meta.update(format="photo_card", theme=theme)
            img = _ed_photo_card(photo, content.get("card_headline") or content.get("key_line", ""),
                                 content.get("card_emphasis", ""), name, (company or {}).get("designation", ""),
                                 avatar_img, theme=_card_look(theme, company), brand_accent=_brand_accent(company),
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

    scene = _ai_photo(content, company, ratio="4:5") if concept else None
    if scene is not None:
        meta.update(format="scene", theme="")
        buf = io.BytesIO()
        _cover_crop(scene, IMG_POST_W, IMG_POST_H).save(buf, format="PNG")
        return buf.getvalue()

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
likes. Build a keepable reference the reader thinks "I need this later" about. Build it as a CONTRAST
whenever the content allows: every point slide shows what most people do against what works (in our
2026 study of real decks, contrast decks ran about 2.5x their authors' usual reactions; "N tips" list
decks ran 0.2-0.8x, even when illustrated).

DECK STRUCTURE — a five-part narrative, follow it exactly:
1. hook_slide     — the cover. The single most important slide.
2. context_slide  — slide 2 earns slide 3 (three in four readers stop at the cover): the stakes as a
   number or a moment from the content, the proof, or a preview of the payoff. Max 20 words in all.
   Never the cover's number again, never point 1 early.
3. content_slides — 4 to 6 slides, ONE idea per slide, in a logical progression.
4. recap_slide    — 3 to 5 one-line takeaways the reader can screenshot.
5. cta_slide      — a single, specific next action.
Total deck = 9 to 12 slides (Voyce adds a cheat-sheet slide built from your points): decks of 11-15 pages
did 16% better than decks of 1-10 in a 2026 study of 134,522 posts. Never pad to get there.

ONE IDEA PER SLIDE (the rule most people break):
- Each content slide makes exactly one point. If a slide has two ideas, split it.
- Keep every slide's body under ~25 words: a phone shows the slide at a third of its size, so
  the words are set large and few. Short, high-contrast, skimmable.

HOOK / COVER SLIDE (80% of success) — headline uses ONE of these proven formulas:
- Contrarian:         "Stop doing X. Do Y instead."
- Curiosity + pain:   "X mistakes costing you Y" (never "quietly killing": every deck that saw it as
  the example used it, and it is one of LinkedIn's most worn covers)
- Result + timeframe: "How I did X in Y days/weeks"
- Framework promise:  "The X framework that got Y"
- Data authority:     "New data: X. Here's what wins." — ONLY with a real, named source in the
  deck; NEVER first-person research claims ("I analyzed", "I studied", "my data") unless the
  provided content proves the author actually did it.
Make it specific (use a real number/name where you can), and pick the formula that fits this
content best. A moment, a surprising number or a contrarian claim; never a question (question openers did
worst in two 2026 studies). The cover carries a NUMBER ("5 mistakes", "24 years", "₹2.4 Cr", "3 checks") or an
"X vs Y" ("Busy vs effective", "Old LinkedIn vs new"): 8 of the 9 top-performing carousel covers in our
2026 study of real posts did. Use one unless the content truly has neither (never invent a number).
When the content sets two ways against each other, build the whole deck as a comparison (each point
slide one pair, what most do vs what works, on the same layout): the top 3 carousels were all comparisons. Don't open with "Stop" when another formula works: it is the most worn cover on
LinkedIn. NEVER generic titles like "Tips for X",
"X Is Important", "Why X Matters", "The Future of X". subtext adds a fact or the promise — never
restates the headline. audience = who the deck is for in 2-6 words ("OEM partner leaders", "First-time
founders"), from the content or the author's profile; empty if it isn't clear. unit = what the content
slides are, one plural word ("plays", "steps", "mistakes", "rules").

CONTENT SLIDES — each has a "kind":
- "point" (default): title = the point as a bold claim (max 6 words); body = ONE concrete example,
  company or number that proves the point (max ~18 words: the slide must read on a phone), never the
  advice in other words: wrong and right already say it, and a body that repeats them says it twice. wrong = what most people
  do instead, concrete, max 8 words ("Promote the top closer to manager"); right = the move that
  works, max 8 words, starts with a verb ("Write the method down first"). The slide shows them as two
  big cards, crossed out and ticked: they are the slide, so make the difference sharp and concrete. label = a short tag ("STEP 1", "MISTAKE 2", "MYTH",
  "TRUTH") fitting the arc, in the cover's unit: a deck of mistakes says MISTAKE 1, MISTAKE 2,
  never LESSON 1. action = leave empty when right is filled (the ticked line is the move); else the
  one move the reader can make (max 10 words, starts with a verb). tag = the point's
  name in 2-4 words, for the deck's at-a-glance slide ("Who monitors", "Price after scope").
  icon = from: pulse, alert, network, link, growth, decline, money, clock, people, target, check,
  shield, idea, gear, calendar, chat, search, doc, ONLY when it literally pictures the point (money
  for a price, clock for time, people for a team); "" otherwise: a decorative icon is noise.
- "stat": use when one number IS the story. stat = the number ("43%", "3x", "₹2.4 Cr"); title =
  what it means; body = one line of context. Use at most one stat slide.
- ONE visual slide (optional, at most one per deck, and not with a stat slide) that draws a point
  instead of saying it: text-only slides cost 15-25% in van der Blom's study. Pick the kind the content
  gives you, place it after the point it proves:
  "chart": title = the claim the numbers prove (max 12 words); bars = 2-5 of {"label": max 4 words,
    "value": exactly as the content states it, e.g. "28%"}; pick = the bar the claim is about (0-based);
    source. Only numbers the content states.
  "flow": title (max 10 words) + steps (3-5, in order, each max 4 words) + mark (0-based: the step most
    people start at or skip) + note (max 4 words, e.g. "Most start here").
  "table": title + cols (2-3 headers, max 3 words each) + rows (3-5 lists of cells, max 4 words each, one
    cell per column) + example (true when row 1 is a filled example from the content and the other rows
    are left blank for the reader).
  Never a visual that only restates a point slide.
- source (any content slide) = where its number or study comes from, exactly as the content given
  names it, in 2-5 words ("Gartner, Oct 2025"); empty when the slide has none. Never the author or
  their own firm, and never a source the content doesn't name.

PULL QUOTE (pull_quote): the deck's sharpest line in 7-12 words, in the author's voice, set alone on a dark
slide halfway through; never a slide title copied; empty when none stands out.

RECAP SLIDE: title = a short recap heading; bullets = 3-5 one-liners, each a point's name in max 7 words,
so the deck reads at a glance; never a slide's line copied word for word.

WORKSHEET SLIDE (worksheet_slide, optional; it takes the recap's place): when the deck teaches a method the
reader can apply to their OWN work, turn it into a sheet they fill in: title = what they'll end up with
(max 8 words, e.g. "Your one-sentence test"); prompts = 2-4 fill-in questions in the deck's order, each max
10 words, answerable in a line ("What do you want clients to repeat?"); example = the answer to prompt 1
for a real case the content names, max 12 words, so the sheet shows what an answer looks like (empty when
the content has no real case: never invent one). The best decks end on a sheet people
save to use. Leave it empty when the deck is a story, a teardown or an opinion.

CTA SLIDE: headline = the ONE thing to remember (a declaration). cta = a SINGLE save ask tied to the
topic ("Save this for your next board review"). NEVER a question (an open question at the end cost 7% of
engagement in a 2026 study of 134,522 posts), never an offer of a template or guide unless the content
says the author has one, and never engagement bait ("Comment YES", "Like if you agree", "Tag a friend").
follow = what the author posts about, 2-5 words, from their profile ("cloud and data decisions"): the slide
says "Follow <name> for <follow>" beside their photo.

VOICE: if a voice/author profile is provided, write every slide the way that author talks.

NEVER FABRICATE: do not invent first-person claims ("My conversion jumped", "I built", "we
grew", "I analyzed") unless that exact claim appears in the provided content or author profile.
Attribute third-party results to their source ("Razorpay's report found...", "Founders
interviewed said..."). Any stat anywhere in the deck must name its source on the same slide or
be cut — an unverifiable number is worse than no number. Numbers come only from the content or
context given, never from memory.

COPY (strict): at most ONE "X, not Y" / "not X but Y" / "isn't X, it's Y" line in the whole deck: it is
the most retained AI structure and readers mark it. No point slide restates slide 2 or another point.
Every point slide carries one specific from the content: a name, a number, a tool or a real example.

OUTPUT HYGIENE (strict):
- No markdown (**, ##, backticks), no placeholder tokens like [X] or [Company], no numbered
  prefixes in titles.
- Banned: game-changer, landscape, unlock, dive deep, revolutionize, leverage, synergy,
  in today's world, the future is here, are you ready, I'm excited, at the end of the day,
  paradigm shift, move the needle, let that sink in, quietly killing.

post_text: the LinkedIn caption. Hook first (max 60 characters, self-contained, open loop),
no warm-up. 60-120 words total, plain words a 15-year-old uses daily, most sentences under
12 words, varied rhythm. No hashtags.
Caption layout: the hook alone on the first line, then 2-3 short paragraphs of 2-3 sentences
each (a blank line between paragraphs), then one closing line. Never put every sentence on its
own line: a caption of one-line sentences reads as a stack of fragments, not a post.

Return ONLY valid JSON."""

_CAROUSEL_SCHEMA = """{
  "hook_slide": {"headline": "...", "subtext": "...", "audience": "...", "unit": "steps", "wrong_head": "Most teams", "right_head": "Do this"},
  "context_slide": {"title": "...", "body": "..."},
  "content_slides": [
    {"kind": "point", "label": "STEP 1", "title": "...", "body": "...", "wrong": "...", "right": "...", "action": "...", "tag": "...", "icon": "...", "source": ""},
    {"kind": "stat", "label": "THE NUMBER", "stat": "43%", "title": "...", "body": "...", "source": "..."},
    {"kind": "point", "label": "STEP 2", "title": "...", "body": "...", "wrong": "...", "right": "...", "action": "...", "tag": "...", "icon": "...", "source": ""},
    {"kind": "chart|flow|table (one at most; or leave it out)", "title": "...", "bars": [{"label": "...", "value": "..."}], "pick": 0, "steps": ["..."], "mark": 0, "note": "...", "cols": ["..."], "rows": [["...", "..."]], "example": false, "source": ""}
  ],
  "pull_quote": "...",
  "recap_slide": {"title": "The recap", "bullets": ["...", "...", "..."]},
  "worksheet_slide": {"title": "...", "prompts": ["...", "..."], "example": "..."},
  "cta_slide": {"headline": "...", "cta": "...", "follow": "..."},
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


# ── deck guards (22 Nov 2026 audit, round 2) ───────────────────────────────────────────────────────
_NUM_WORDS = ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten"]
_STOP = set("a an the and or but to of in on at for with by from is are was were be been it its this that these those "
            "you your yours we our i my me they their them he she his her not no don't doesn't isn't can't won't do does "
            "did as so if than then just only every each all any more most into out up over about one".split())


def _one_of(unit: str) -> str:
    if unit.endswith("ies"):
        return unit[:-3] + "y"
    if unit.endswith(("xes", "ches", "shes", "sses", "zes")):
        return unit[:-2]
    return unit[:-1] if unit.endswith("s") else unit


def _fix_count(text: str, n: int, unit: str) -> str:
    """'Here are 4 steps' in a deck of 5 steps: the number before the unit is set to the deck's count (Raj's deck,
    22 Nov 2026). Digits stay digits, words stay words."""
    if not text or not unit or n < 1:
        return text
    forms = "|".join(sorted({re.escape(unit), re.escape(_one_of(unit))}, key=len, reverse=True))
    pat = re.compile(rf"\b(\d{{1,2}}|{'|'.join(_NUM_WORDS[2:])})(\s+(?:[A-Za-z-]+\s+){{0,2}}?(?:{forms}))\b", re.I)

    def swap(m):
        said = m.group(1)
        if said.isdigit():
            return (str(n) if int(said) != n else said) + m.group(2)
        if _NUM_WORDS.index(said.lower()) == n or n > 10:
            return m.group(0)
        word = _NUM_WORDS[n]
        return (word.capitalize() if said[0].isupper() else word) + m.group(2)
    return pat.sub(swap, text)


def _stem(w: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if w.endswith(suf) and len(w) - len(suf) >= 3:
            return w[:-len(suf)]
    return w


def _content_words(text: str) -> set:
    return {_stem(w) for w in re.findall(r"[a-z0-9'%₹$]+", (text or "").lower())
            if w not in _STOP and w not in ("how", "much", "many", "what", "when", "where", "why") and len(w) > 1}


def _drop_repeats(body: str, wrong: str, right: str) -> str:
    """A body sentence that only says the crossed-out or ticked line again goes: 'Don't plan the whole exit at
    once.' above a crossed-out 'Plan the whole exit at once' said it twice (Raj's deck, 22 Nov 2026)."""
    said = _content_words(wrong) | _content_words(right)
    if not body or not said:
        return body
    keep = []
    for sent in re.split(r"(?<=[.!?])\s+", body.strip()):
        w = _content_words(sent)
        if w and len(w & said) / len(w) >= 0.6:
            continue
        keep.append(sent)
    return " ".join(keep).strip()


def _sentence_case(text: str, deck_text: str) -> str:
    """'4 Answers Before Any Layoff Number' → '4 answers before any layoff number': a Title Case headline read as
    a template next to sentence-case ones. A word the deck capitalises mid-sentence (a name) keeps its capital."""
    words = (text or "").split()
    rest = [w for w in words[1:] if re.match(r"^[A-Za-z][a-z'’-]+[.,:;!?]?$", w) or re.match(r"^[A-Z][a-z'’-]*[.,:;!?]?$", w)]
    alpha = [w for w in words[1:] if re.match(r"^[A-Za-z]", w)]
    if len(words) < 4 or not alpha or sum(w[0].isupper() for w in rest) < 0.7 * len(alpha):
        return text

    def is_name(w):
        core = re.sub(r"[^A-Za-z'’-]", "", w)
        if not core or not core[0].isupper() or core[1:].lower() != core[1:]:
            return True   # FOMO, ServiceNow, AWS: never touched
        return bool(re.search(rf"(?<![.!?:]\s)(?<!^)(?<=\s){re.escape(core)}\b", deck_text or ""))
    out = [words[0]]
    for w in words[1:]:
        out.append(w if is_name(w) or w in ("I", "I'm", "I've") else w[0].lower() + w[1:])
    return " ".join(out)


def _guard_deck(result: dict) -> dict:
    hook, ctx = result.get("hook_slide") or {}, result.get("context_slide")
    # a chart, flow or table slide draws a point; it isn't one of the cover's points ("5 lessons" over 4, 22 Nov 2026)
    pts = [s for s in result.get("content_slides") or [] if s.get("kind") not in ("stat", "chart", "flow", "table")]
    for s in pts:
        if s.get("wrong") and s.get("right"):
            s["body"] = _drop_repeats(s.get("body", ""), s["wrong"], s["right"])
    unit = hook.get("unit") or ""
    one = _one_of(unit) if unit else ""
    counted = [s for s in pts if not one or one in (s.get("label") or "").lower() or not s.get("label")]
    if unit and counted:
        n = len(counted)
        hook["headline"] = _fix_count(hook.get("headline", ""), n, unit)
        hook["subtext"] = _fix_count(hook.get("subtext", ""), n, unit)
        if ctx:
            ctx["title"] = _fix_count(ctx.get("title", ""), n, unit)
            ctx["body"] = _fix_count(ctx.get("body", ""), n, unit)
    deck_text = " ".join([(ctx or {}).get("body", "")] + [s.get("body", "") for s in result.get("content_slides") or []]
                         + [result.get("post_text") or ""])
    hook["headline"] = _sentence_case(hook.get("headline", ""), deck_text)
    for s in result.get("content_slides") or []:
        s["title"] = _sentence_case(s.get("title", ""), deck_text)
    if ctx:
        ctx["title"] = _sentence_case(ctx.get("title", ""), deck_text)
    return result


def _visual_slide(s: dict, source_text: str | None) -> dict | None:
    """A chart, flow or table slide from the writer, checked: a chart keeps only numbers the source states (any
    number when re-cleaning a deck the user already has); a flow needs 3-5 short steps; a table 2-3 columns and
    2-5 rows. None when it doesn't hold together."""
    kind = s.get("kind")
    out = {"kind": kind, "title": _clean_slide_text(s.get("title", "")) if s.get("title") else "",
           "source": _clean_slide_text(str(s.get("source", "")).split(";")[0])[:48] if s.get("source") else ""}
    if not out["title"]:
        return None
    try:
        idx = int(s.get("pick", s.get("mark", -1)) if s.get("pick", s.get("mark")) is not None else -1)
    except (TypeError, ValueError):
        idx = -1
    if kind == "chart":
        if source_text is None:
            bars = []
            for b in s.get("bars") or []:
                if isinstance(b, dict):
                    m = re.search(r"\d+(?:[.,]\d+)?", str(b.get("value") or "").replace(",", ""))
                    if m and str(b.get("label") or "").strip():
                        bars.append({"label": " ".join(str(b["label"]).split()[:5]), "value": str(b["value"]).strip()[:12],
                                     "num": float(m.group(0))})
            bars = bars[:5]
        else:
            bars = _chart_bars(s.get("bars"), source_text)[:5]
        if len(bars) < 2:
            return None
        out.update(bars=bars, pick=idx if 0 <= idx < len(bars) else 0)
    elif kind == "flow":
        steps = [st for st in _short_list(s.get("steps"), 5, 6)]
        if len(steps) < 3:
            return None
        out.update(steps=steps, mark=idx if 0 <= idx < len(steps) else -1,
                   note=" ".join(_clean_slide_text(s.get("note", "")).split()[:5]) if s.get("note") else "")
    elif kind == "table":
        cols = _short_list(s.get("cols"), 3, 4)
        rows = []
        for r in s.get("rows") or []:
            if isinstance(r, list):
                cells = [" ".join(_clean_slide_text(str(c)).split()[:5]) if str(c).strip() else "" for c in r][:len(cols)]
                cells += [""] * (len(cols) - len(cells))
                rows.append(cells)
        rows = rows[:5]
        if len(cols) < 2 or len(rows) < 2:
            return None
        out.update(cols=cols, rows=rows, example=bool(s.get("example")) and any(rows[0]))
    else:
        return None
    return out


_NOT_X = re.compile(r"\b(?:not|never|isn['’]t|aren['’]t|wasn['’]t)\b[^.!?]{1,60}?[,;—–-]\s*(?:but\s+|it['’]s\s+)?\w|"
                    r",\s*not\s+\w", re.I)


def _deck_lines(result: dict) -> list:
    """(slot, key, text) for every line a reader sees on the deck's slides."""
    out = []
    hook, ctx = result.get("hook_slide") or {}, result.get("context_slide") or {}
    out += [(hook, k, hook.get(k, "")) for k in ("headline", "subtext")]
    out += [(ctx, k, ctx.get(k, "")) for k in ("title", "body")] if ctx else []
    for c in result.get("content_slides") or []:
        out += [(c, k, c.get(k, "")) for k in ("title", "body", "wrong", "right") if c.get(k)]
    if result.get("pull_quote"):
        out.append((result, "pull_quote", result["pull_quote"]))
    cta = result.get("cta_slide") or {}
    out.append((cta, "headline", cta.get("headline", "")))
    return [o for o in out if o[2]]


def _one_not_x(result: dict) -> dict:
    """At most one "X, not Y" line in a deck (22 Nov 2026 carousel audit: 2-4 per deck, the most retained AI
    structure): the others are rewritten in one short call, same meaning, the first kept. Fail-open."""
    hits = [o for o in _deck_lines(result) if _NOT_X.search(o[2])]
    if len(hits) < 2:
        return result
    extra = hits[1:6]
    try:
        fix = generate_json("Rewrite each line so it no longer uses a contrast built on 'not' (\"X, not Y\", \"not X but Y\", "
                            "\"isn't X, it's Y\"). Same meaning, same or fewer words, plain words, no new facts. Return JSON "
                            "{\"lines\": [...]} in the same order.\n\n" + "\n".join(f"{i + 1}. {o[2]}" for i, o in enumerate(extra)),
                            system="You edit LinkedIn carousel lines. Keep every fact and the author's voice.",
                            max_tokens=600, temperature=0.3, model=CLAUDE_CAROUSEL_MODEL, effort=CLAUDE_CAROUSEL_EFFORT)
        new = [str(x).strip() for x in (fix or {}).get("lines") or []]
        if len(new) == len(extra):
            for (slot, key, old), line in zip(extra, new):
                if line and len(line.split()) <= len(old.split()) + 3:
                    slot[key] = _clean_slide_text(line)
    except Exception:
        logging.exception("[Carousel] could not rewrite the extra 'not X' lines")
    return result


def _sanitize_carousel_result(result: dict, company: dict = None, source_text: str | None = None) -> dict:
    hook = result.get("hook_slide") or {}
    hook["headline"] = _clean_slide_text(hook.get("headline", ""))
    hook["subtext"] = _clean_slide_text(hook.get("subtext", ""))
    aud = _clean_slide_text(hook.get("audience", "")) if hook.get("audience") else ""
    hook["audience"] = aud if len(aud) <= 36 else ""          # longer is cut mid-word on the cover
    unit = re.sub(r"[^a-z]", "", str(hook.get("unit") or "").lower())
    hook["unit"] = unit if 3 <= len(unit) <= 12 else ""
    for k in ("wrong_head", "right_head"):   # who does each, named once for the deck ("Most boards" / "Do this")
        v = " ".join(_clean_slide_text(hook.get(k, "")).split()[:4]) if hook.get(k) else ""
        hook[k] = v if len(v) <= 26 else ""
    result["hook_slide"] = hook

    ctx = result.get("context_slide") or {}
    ctx_title = _clean_slide_text(ctx.get("title", "")) if ctx.get("title") else ""
    ctx_body = _clean_slide_text(ctx.get("body", "")) if ctx.get("body") else ""
    result["context_slide"] = {"title": ctx_title, "body": ctx_body} if (ctx_title or ctx_body) else None

    cleaned_content, visual_done = [], False
    for s in (result.get("content_slides") or []):
        s = s or {}
        kind = s.get("kind", "point")
        if kind in ("chart", "flow", "table"):
            v = None if visual_done else _visual_slide(s, source_text)
            if v:
                cleaned_content.append(v)
                visual_done = True
            continue
        cleaned = {
            "kind":  kind if kind in ("point", "stat") else "point",
            "label": _clean_slide_text(s.get("label", ""))[:24] if s.get("label") else "",
            "title": _clean_slide_text(s.get("title", "")) if s.get("title") else "",
            "body":  _clean_slide_text(s.get("body", "")) if s.get("body") else "",
            "action": _clean_slide_text(s.get("action", ""))[:110] if s.get("action") else "",
            # what most do vs what works: a crossed-out and a ticked line on the slide (22 Nov 2026 audit)
            "wrong": " ".join(_clean_slide_text(s.get("wrong", "")).split()[:10])[:70] if s.get("wrong") else "",
            "right": " ".join(_clean_slide_text(s.get("right", "")).split()[:10])[:70] if s.get("right") else "",
            # the at-a-glance slide's name for the point, and its icon (designs.py)
            "tag":   " ".join(_clean_slide_text(s.get("tag", "")).split()[:4])[:28] if s.get("tag") else "",
            "icon":  str(s.get("icon") or "").strip().lower() if str(s.get("icon") or "").strip().lower() in CARD_ICONS else "",
            # one source, short: a long one was cut mid-word on the slide
            "source": _clean_slide_text(str(s.get("source", "")).split(";")[0])[:48] if s.get("source") else "",
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

    ws = result.get("worksheet_slide") or {}
    prompts = [" ".join(_clean_slide_text(p).split()[:12]) for p in (ws.get("prompts") or []) if p and str(p).strip()][:4]
    result["worksheet_slide"] = ({"title": " ".join(_clean_slide_text(ws.get("title", "")).split()[:9]) if ws.get("title") else "",
                                  "prompts": prompts,
                                  "example": " ".join(_clean_slide_text(ws.get("example", "")).split()[:14]) if ws.get("example") else ""}
                                 if len(prompts) >= 2 else None)
    # a chart with a stat slide said the numbers twice: the stat goes
    if visual_done and any(c.get("kind") == "chart" for c in cleaned_content):
        result["content_slides"] = [c for c in result["content_slides"] if c.get("kind") != "stat"]
    pq = " ".join(_clean_slide_text(result.get("pull_quote", "")).split()) if result.get("pull_quote") else ""
    titles = {(c.get("title") or "").lower().strip(" .") for c in result["content_slides"]}
    result["pull_quote"] = pq if 5 <= len(pq.split()) <= 14 and pq.lower().strip(" .") not in titles else ""

    cta = result.get("cta_slide") or {}
    cta["headline"] = _clean_slide_text(cta.get("headline", ""))
    cta["cta"] = _clean_slide_text(cta.get("cta", ""))
    if cta["cta"].rstrip().endswith("?"):
        # an open question at the end cost 7% of engagement (22 Nov 2026 carousel audit): the save ask instead
        cta["cta"] = "Save this and fill in the sheet this week." if result.get("worksheet_slide") else "Save this for later."
    cta["follow"] = " ".join(_clean_slide_text(cta.get("follow", "")).split()[:6]) if cta.get("follow") else ""
    result["cta_slide"] = cta

    if "post_text" in result:
        from generator import _strip_markdown, drop_author_furniture
        result["post_text"] = _caption_paragraphs(drop_author_furniture(_strip_markdown(result.get("post_text") or ""), company))
    return _guard_deck(result)


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
- hook_slide (cover): headline uses ONE proven formula, max 6 words (it is set huge), specific, in sentence case (a capital only on the first word and on names), with a number or an "X vs Y" in it whenever the content has one, best at the start ("5 controls before…": the number is drawn huge). No colon labels ("Cloud exit: …"). subtext: one line, max 14 words. audience + unit. A number of things on the cover ("4 gaps") means exactly that many point slides labelled in that unit: count them before you answer. A slide that isn't one of them (PROOF, MYTH) isn't counted.
- context_slide: title (max 8 words) + body, max 20 words in all: the stakes (a number or moment from the content), the proof, or the payoff preview. Never the cover's number again. Never say how many points follow: the cover does.
- content_slides: 4-6 slides, ONE idea each. title max 6 words as a claim; body max ~18 words: the EVIDENCE for the point (a company, number, quote or example from the content), never the advice again, because wrong and right carry the advice; wrong and right differ in what the reader DOES, not in one word or a date; source where it fits; every point slide gets a tag (2-4 words); an icon only when it literally pictures the point.
- recap_slide: title + 3-5 bullets, each the point's name in max 7 words: never a slide's line copied.
- worksheet_slide (only for a method the reader applies to their own work): title + 2-4 fill-in prompts, max 10 words each.
- one visual slide (chart, flow or table) when the content gives you the numbers, the order or the grid; never with a stat slide.
- worksheet example: the answer to prompt 1 for a real case the content names, or empty.
- pull_quote: 7-12 words, or empty.
- cta_slide: headline (the one takeaway) + a SINGLE save ask (never a question, no engagement bait) + follow (2-5 words).
- copy: at most one "X, not Y" line in the deck; one specific per point slide; no slide restates slide 2.
Aim for 9-12 total slides. Build it as a contrast (what most do vs what works) whenever the content allows."""


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

    result = generate_json(prompt, system=_CAROUSEL_SYSTEM, max_tokens=6000, temperature=0.85,
                           model=CLAUDE_CAROUSEL_MODEL, effort=CLAUDE_CAROUSEL_EFFORT)   # Sonnet: better and a third of the cost (llm.py)
    return _one_not_x(_sanitize_carousel_result(result, company, source_text=raw_text))


# ── Autonomous content generation ────────────────────────────────────────────

def generate_carousel_content(company: dict, news_context: str, post_type: str) -> dict:
    from autonomous import _build_company_brief, POST_TYPE_LABELS, _hook_guidance

    is_personal = company.get("profile_type") == "personal"
    voice_note = ("First person (I, my, I've) — write as if the author is speaking directly."
                  if is_personal else "Third person — company voice, authoritative.")
    company_brief = _build_company_brief(company, with_knowledge=False)[:1400]
    # Their own facts and rules get their own place: inside the brief they came after the voice
    # examples, so the 1,400-character cut above always dropped them.
    from generator import author_rules
    knowledge = author_rules(company)   # whole: a 2,000 cut lost a long box's writing rules (8 Nov 2026)
    rules = (f"\nThe author's own facts and rules (follow every one that fits a carousel; never "
             f"contradict them or invent around them; a line quoted in them shows a style, never "
             f"words to reuse):\n{knowledge}\n") if knowledge else ""
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
Content-slide bodies must cite real companies, reports or numbers from the context above (never a number from memory) — nothing that could apply to any industry.

{voice_note}
{_hook_guidance(company.get("allowed_hooks"))}
{avoid}
The cover slide's title and the caption's first line count as openings.

Return ONLY valid JSON in this shape:
{_CAROUSEL_SCHEMA}"""

    result = generate_json(prompt, system=_CAROUSEL_SYSTEM, max_tokens=6000, temperature=0.85,
                           model=CLAUDE_CAROUSEL_MODEL, effort=CLAUDE_CAROUSEL_EFFORT)   # Sonnet: better and a third of the cost (llm.py)
    return _one_not_x(_sanitize_carousel_result(result, company, source_text="\n".join([news_context or "", company_brief, knowledge or ""])))


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


def _edc_cover(headline: str, subtext: str, total: int, t: dict, name: str, title: str, avatar,
               audience: str = "", promise: str = "") -> Image.Image:
    S, m = _SCALE, 84 * _SCALE
    img = Image.new("RGB", (_RW, _RH), t["bg"]); draw = ImageDraw.Draw(img)
    max_w = _RW - m * 2
    # Who it's for, when the writer named them: the reader sees at a glance that it's theirs
    eyebrow = f"For {audience}" if audience else ("Swipe through" if promise else f"Swipe through · {total} slides")
    top = _edc_eyebrow(draw, eyebrow, m, 100 * S, t)
    _ed_logo(img, t, 86 * S)
    base, kicker = _split_hook_pill(headline)
    full = (base + " " + kicker).strip() if kicker else headline
    sig_y = _RH - m - 72 * S
    bottom = sig_y - 70 * S
    if promise:                  # "5 plays · 2-minute read · save it", in a pill above the author
        f_p = _font(28 * S, bold=True)
        ph = _ed_line_h(draw, f_p) + 26 * S
        py = sig_y - 44 * S - ph
        pw = _tw(draw, promise, f_p) + 52 * S
        draw.rounded_rectangle([m, py, m + pw, py + ph], radius=ph // 2, outline=t["muted"], width=max(2, 2 * S))
        draw.text((m + 26 * S, py + 13 * S), promise, font=f_p, fill=t["ink"])
        bottom = py - 52 * S
    f_head, h_head = _ed_fit(draw, full, max_w, int((bottom - top) * 0.74), (156, 146, 136, 124, 112, 100, 90, 80, 72), 12 * S, 6, _font_black)
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
               numeral: str = "", eyebrow: str = "", action: str = "", source: str = "") -> Image.Image:
    S, m = _SCALE, 84 * _SCALE
    img = Image.new("RGB", (_RW, _RH), t["bg"]); draw = ImageDraw.Draw(img)
    max_w = _RW - m * 2
    bottom = _edc_footer(img, draw, t, num, total, name, avatar)
    _ed_logo(img, t, 86 * S)
    action = (action or "").strip()
    if action:                   # the reader's move this week, ruled off at the foot of the slide
        f_al, f_a = _font(22 * S, bold=True), _font(38 * S, bold=True)
        lines = _wrap(draw, action, f_a, max_w)
        if len(lines) > 2:
            lines = [lines[0], _fit_one_line(draw, " ".join(lines[1:]), f_a, max_w)]
        lh_a = _ed_line_h(draw, f_a) + 8 * S
        ay = bottom - len(lines) * lh_a - _ed_line_h(draw, f_al) - 18 * S
        draw.rectangle([m, ay - 34 * S, _RW - m, ay - 32 * S], fill=t["ink"])
        draw.text((m, ay), (t.get("action_label") or "Try this week").upper(), font=f_al, fill=t["muted"])
        ly = ay + _ed_line_h(draw, f_al) + 18 * S
        for ln in lines:
            draw.text((m, ly), ln, font=f_a, fill=t["ink"])
            ly += lh_a
        bottom = ay - 34 * S - 56 * S
    top = 110 * S
    if eyebrow:
        top = _edc_eyebrow(draw, eyebrow, m, top, t)
    source = (source or "").strip()
    f_src = _font(24 * S)
    h_src = (_ed_line_h(draw, f_src) + 12 * S) if source else 0
    body = (body or "").strip()
    gap_tb = 52 * S + 10 * S + 52 * S
    # A smaller numeral and title until the body fits: with an action box below, the first
    # sizes left a long body running into it
    for num_size, share in ((250, 0.48), (200, 0.40), (160, 0.32)):
        f_num = _font_black(num_size * S)
        h_num = (_ed_line_h(draw, f_num) + 24 * S) if numeral else 0
        avail = bottom - top - h_num - h_src
        f_t, h_t = _ed_fit(draw, title, max_w, int(avail * share), (112, 104, 96, 88, 80, 72, 64, 56), 14 * S, 3, _font_black)
        f_b, h_b = _ed_fit(draw, body, max_w, max(0, avail - h_t - gap_tb), (56, 52, 48, 44, 40, 36, 32), 16 * S, 7, _font) if body else (None, 0)
        if h_t + (gap_tb + h_b if body else 0) <= avail:
            break
    block = h_num + h_t + (gap_tb + h_b if body else 0) + h_src
    y = top + max(0, (bottom - top - block) // 2)
    if numeral:
        draw.text((m - 6 * S, y - 30 * S * num_size // 250), numeral, font=f_num, fill=t["accent"])
        y += h_num
    y = _ed_draw_marked(draw, title, f_t, m, y, max_w, t, "", 14 * S)
    if body:
        y += 52 * S - 14 * S
        draw.rectangle([m, y, m + 110 * S, y + 12 * S], fill=t["marker"])
        y += 10 * S + 52 * S
        y = _ed_draw_marked(draw, body, f_b, m, y, max_w, t, "", 16 * S, color=t["body"])
    if source:                   # where the slide's number comes from
        draw.text((m, y - 4 * S), _fit_one_line(draw, f"Source: {source}", f_src, max_w), font=f_src, fill=t["muted"])
    return img.resize((SLIDE_W, SLIDE_H), Image.LANCZOS)


def _edc_stat(stat: str, title: str, body: str, num: int, total: int, t: dict, name: str, avatar,
              source: str = "") -> Image.Image:
    S, m = _SCALE, 84 * _SCALE
    tc = _ed_contrast(t)
    img = Image.new("RGB", (_RW, _RH), tc["bg"]); draw = ImageDraw.Draw(img)
    max_w = _RW - m * 2
    bottom = _edc_footer(img, draw, tc, num, total, name, avatar)
    _ed_logo(img, tc, 86 * S)
    source = (source or "").strip()
    if source:                   # where the number comes from, under everything else
        f_src = _font(26 * S)
        bottom -= _ed_line_h(draw, f_src) + 30 * S
        draw.text((m, bottom + 30 * S), _fit_one_line(draw, f"Source: {source}", f_src, max_w), font=f_src, fill=tc["muted"])
    top = (176 if tc.get("logo") else 110) * S   # a full-width number ran up under the logo
    body = (body or "").strip()
    sizes = [z for z in (330, 290, 250, 210, 180, 150, 120) if _tw(draw, stat, _font_black(z * S)) <= max_w] or [120]
    for z in sizes:              # the biggest number that still leaves the title and body room
        f_s = _font_black(z * S)
        h_s = _ed_line_h(draw, f_s)
        f_t, h_t = _ed_fit(draw, title, max_w, 320 * S, (76, 70, 64, 58, 52), 12 * S, 3, lambda z: _font(z, bold=True))
        room = bottom - top - h_s - 30 * S - h_t - 36 * S
        f_b, h_b = _ed_fit(draw, body, max_w, max(0, min(380 * S, room)), (48, 44, 40, 36, 32), 14 * S, 5, _font) if body else (None, 0)
        block = h_s + 30 * S + h_t + (36 * S + h_b if body else 0)
        if block <= bottom - top:
            break
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
    _ed_logo(img, t, 86 * S)
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
    _ed_logo(img, tc, 86 * S)
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
    # "5 plays · 2-minute read" on the cover, when the writer named what its slides are
    unit = (hook.get("unit") or "").strip()
    points = sum(1 for s in c_slides if not (s.get("kind") == "stat" and s.get("stat")))
    words = sum(len(str(v).split()) for part in (hook, context or {}, *c_slides, recap or {}, cta)
                for v in part.values() if isinstance(v, (str, list)) for v in ([v] if isinstance(v, str) else v))
    count = "" if re.search(r"\d", hook.get("headline", "")) else f"{points} {unit} · "   # "3 trust gaps" over "5 plays" read as a mistake
    promise = f"{count}{max(1, -(-words // 200))} min read · save it" if unit and points else ""
    if CARD_DESIGN == "v2":
        try:
            import designs
            font = str(((company or {}).get("brand_kit") or {}).get("font") or "")
            return designs.carousel_slides(content, {**t, "font": font}, {**t_cover, "font": font},
                                           name, title, avatar, promise)
        except Exception:
            logging.exception("[Carousel] deck v2 failed; drawing the editorial one")
    slides = [_edc_cover(hook.get("headline", ""), hook.get("subtext", ""), total, t_cover, name, title, avatar,
                         audience=hook.get("audience", ""), promise=promise)]
    num = 2
    if context:
        slides.append(_edc_point(context.get("title", ""), context.get("body", ""), num, total, t, name, avatar, eyebrow="Start here"))
        num += 1
    step = 0
    for s in c_slides:
        if s.get("kind") == "stat" and s.get("stat"):
            slides.append(_edc_stat(s["stat"], s.get("title", ""), s.get("body", ""), num, total, t, name, avatar,
                                    source=s.get("source", "")))
        else:
            step += 1
            slides.append(_edc_point(s.get("title", ""), s.get("body", ""), num, total, t, name, avatar, numeral=f"{step:02d}",
                                     action=s.get("action", ""), source=s.get("source", "")))
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
