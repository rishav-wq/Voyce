"""Voyce's designed cards and carousel slides, v2 (Nov 2026).

Built from what LinkedIn document and image posts that get read and saved have in common: a cover that
makes a specific promise in a few words, with the author's face and a swipe cue; inner slides of one
idea and few words, set large enough to read when a phone shows the slide at a third of its size; one
visual anchor per slide (a number, an icon, a grid of tiles); the logo on the first and last slide only;
the bottom strip left clear for LinkedIn's page dots; one call to action. Colours come from the look
carousel.py picks (a brand kit look or one of its colour sets). carousel.py calls these and goes back
to its earlier design if one of them raises, so a post never fails on a layout.
"""
import re

from PIL import Image, ImageDraw, ImageFilter

import carousel as C

S = C._SCALE
W, H = C.SLIDE_W * S, C.SLIDE_H * S
M = 92 * S                 # margins
TOP = 176 * S              # inner slides: content starts under the top bar
SAFE = H - 132 * S         # and stops above LinkedIn's page dots

# The icons a writer may name for a point; anything else gets the point's number instead.
ICONS = C.CARD_ICONS
SCALES = (1.0, 0.93, 0.86, 0.79, 0.72)   # a slide's sizes shrink in these steps until its content fits


# ── type ─────────────────────────────────────────────────────────────────────────────────────────
def _display(px: float, t: dict | None = None):
    """The headline face: the brand kit's font when we hold it, else the one every other card uses."""
    return C._display_font(int(px * S), (t or {}).get("font", ""))


def _body(px: float, bold: bool = False, semi: bool = False):
    return C._font(int(px * S), bold=bold, semi=semi)


def _lh(d, f) -> int:
    return d.textbbox((0, 0), "Hg", font=f)[3]


def _tokens(text: str, emph: str) -> list:
    """(word, in the emphasis phrase) for each word; the phrase is marked where it first appears.
    It may break across lines: it is coloured text, not a highlighter box, and keeping it whole
    cost the cover a fifth of its headline size."""
    norm = lambda w: w.strip('.,!?;:"\'“”‘’()').lower()
    words, target = (text or "").split(), [norm(w) for w in (emph or "").split() if norm(w)]
    hot = set()
    for i in range(len(words) - len(target) + 1) if target else []:
        if [norm(w) for w in words[i:i + len(target)]] == target:
            hot = set(range(i, i + len(target)))
            break
    return [(w, i in hot) for i, w in enumerate(words)]


def _wrap(d, text: str, f, max_w: int, emph: str = "") -> list:
    lines, cur, cur_w, sp = [], [], 0, C._tw(d, " ", f)
    for tok in _tokens(text, emph):
        w = C._tw(d, tok[0], f)
        if cur and cur_w + sp + w > max_w:
            lines.append(cur)
            cur, cur_w = [tok], w
        else:
            cur_w += (sp if cur else 0) + w
            cur.append(tok)
    if cur:
        lines.append(cur)
    return lines


def _line_w(d, line, f) -> int:
    return sum(C._tw(d, t, f) for t, _ in line) + C._tw(d, " ", f) * (len(line) - 1)


def _step(d, f) -> int:
    """Line to line: the font's own height, but never looser than 1.14 of its size (the brand
    font's spacing is so loose that four headline lines took the whole cover)."""
    return min(_lh(d, f), int(f.size * 1.14))


def _block_h(d, text: str, f, max_w: int, gap: int, emph: str = "") -> int:
    n = len(_wrap(d, text, f, max_w, emph))
    return n * _step(d, f) + gap * max(0, n - 1) + (_lh(d, f) - _step(d, f)) if n else 0


def _write(d, text: str, f, x: int, y: int, max_w: int, ink, gap: int, emph: str = "", accent=None) -> int:
    """Wrapped text, the emphasis phrase in the accent colour (never a highlighter box). The new y."""
    sp = C._tw(d, " ", f)
    for line in _wrap(d, text, f, max_w, emph):
        px = x
        for t, hot in line:
            d.text((px, y), t, font=f, fill=(accent or ink) if hot else ink)
            px += C._tw(d, t, f) + sp
        y += _step(d, f) + gap
    return y


def _ragged(lines: list) -> bool:
    """A line of one short word between fuller ones ("4 ownership / gaps quietly / killing /
    joint deals"): the next size down usually sets it evenly."""
    return len(lines) > 2 and any(len(ln) == 1 and not ln[0][1] and len(ln[0][0]) < 9 for ln in lines[:-1])


def _fit(d, text: str, sizes, maker, max_w: int, max_lines: int, emph: str = "", even: bool = False):
    """The largest size whose lines all fit the width, in no more than max_lines (and, if even,
    without a lone short word on a line)."""
    for s in sizes:
        f = maker(s)
        lines = _wrap(d, text, f, max_w, emph)
        if len(lines) <= max_lines and all(_line_w(d, ln, f) <= max_w for ln in lines) \
                and not (even and _ragged(lines) and s != sizes[-1]):
            return f
    return maker(sizes[-1])


# ── colour ───────────────────────────────────────────────────────────────────────────────────────
def _light(t) -> bool:
    return C._luminance(t["bg"]) > 0.5


def _loud(t: dict) -> dict:
    """The deck's loud slides (the number, the last slide): a light look turns to its dark side."""
    return C._ed_contrast(t) if _light(t) else t


def _panel(t: dict):
    """The tile behind an icon: the ink on a light page, a lifted shade on a dark one."""
    return t["ink"] if _light(t) else C._mix(t["bg"], (255, 255, 255), 0.09)


def _on(fill, t: dict):
    """Words or a mark on a fill: the page or the ink, whichever stands out more. on_marker was
    made for the highlighter colour, and on a light set's dark accent it put dark on dark."""
    return max((t["bg"], t["ink"]), key=lambda c: abs(C._luminance(c) - C._luminance(fill)))


def _icon_colour(t: dict):
    """An icon on its tile: the accent, unless the accent is too close to the tile to see."""
    tile = _panel(t)
    return t["accent"] if abs(C._luminance(t["accent"]) - C._luminance(tile)) > 0.3 else t["bg"]


_GRAIN = {}


def _page(t: dict, grain: bool = True):
    """A slide's page, with a faint paper grain: flat fills are what make a card look like a template."""
    img = Image.new("RGB", (W, H), t["bg"])
    if grain:
        mask = _GRAIN.get((W, H))
        if mask is None:
            # made at half size and scaled up once: blurring at full size cost a sixth of a gigabyte
            mask = Image.effect_noise((W // 2, H // 2), 22).filter(ImageFilter.GaussianBlur(S * 0.15))
            mask = mask.point(lambda v: 80 if v > 150 else 0).resize((W, H), Image.BILINEAR)
            _GRAIN[(W, H)] = mask
        tint = C._mix(t["bg"], (255, 255, 255) if not _light(t) else (60, 50, 40), 0.06)
        img.paste(tint, (0, 0, W, H), mask)   # in place: a second full-size page per slide ran a server out of memory
    return img, ImageDraw.Draw(img)


def _done(img: Image.Image) -> Image.Image:
    return img.resize((W // S, H // S), Image.LANCZOS)


# ── marks: the author, the logo, labels ─────────────────────────────────────────────────────────
def _photo(img, avatar, name: str, x: int, y: int, size: int, t: dict, ring: bool = True):
    d = ImageDraw.Draw(img)
    if ring:
        rw = max(2, size // 22)
        d.ellipse([x - rw, y - rw, x + size + rw, y + size + rw], fill=t["accent"])
    if avatar is not None:
        av = avatar.convert("RGB").resize((size, size), Image.LANCZOS)
        m = Image.new("L", (size, size), 0)
        ImageDraw.Draw(m).ellipse([0, 0, size, size], fill=255)
        img.paste(av, (x, y), m)
        return
    d.ellipse([x, y, x + size, y + size], fill=_panel(t))
    f = _body(size / S * 0.46, bold=True)
    ini = ((name or "V").strip()[:1] or "V").upper()
    b = d.textbbox((0, 0), ini, font=f)
    d.text((x + (size - (b[2] - b[0])) // 2 - b[0], y + (size - (b[3] - b[1])) // 2 - b[1]), ini, font=f, fill=_icon_colour(t))


def _logo(img, t: dict, x_right: int, y: int, h: int) -> int:
    """The brand kit's logo, in the version made for this background; its width (0 if none)."""
    logo = (t.get("logo") or {}).get("dark" if not _light(t) else "light")
    if logo is None or t["bg"] == t["accent"]:
        return 0
    w = int(logo.width * h / logo.height)
    mark = logo.resize((w, h), Image.LANCZOS)
    img.paste(mark, (x_right - w, y), mark)
    return w


def _title_line(title: str) -> str:
    return (title or "").strip()


def _author(img, d, t: dict, avatar, name: str, title: str, x: int, y: int, size: int,
            name_px: float, title_px: float, max_w: int):
    _photo(img, avatar, name, x, y, size, t)
    tx = x + size + 22 * S
    fn, ft = _body(name_px, bold=True), _body(title_px)
    title = _title_line(title)
    if title:
        d.text((tx, y + size // 2 - _lh(d, fn) - 2 * S), C._fit_one_line(d, name, fn, max_w), font=fn, fill=t["ink"])
        d.text((tx, y + size // 2 + 6 * S), C._fit_one_line(d, title, ft, max_w), font=ft, fill=t["muted"])
    else:
        d.text((tx, y + (size - _lh(d, fn)) // 2), C._fit_one_line(d, name, fn, max_w), font=fn, fill=t["ink"])


def _eyebrow(d, text: str, x: int, y: int, col, px: float = 23) -> int:
    """A short letter-spaced label; the y under it."""
    f = _body(px, semi=True)
    cx = x
    for ch in (text or "").upper()[:44]:
        d.text((cx, y), ch, font=f, fill=col)
        cx += C._tw(d, ch, f) + 3 * S
    return y + _lh(d, f)


def _eyebrow_w(d, text: str, px: float) -> int:
    """How wide _eyebrow draws a label: in capitals, letter-spaced."""
    f = _body(px, semi=True)
    return sum(C._tw(d, ch, f) + 3 * S for ch in (text or "").upper()[:44])


def _fit_eyebrow(d, text: str, max_w: int, sizes: tuple = (30, 27, 24)) -> tuple:
    """A label that fits max_w as it is drawn: a size smaller first, then cut at a whole word with "…"
    (a long audience once ran into a wide logo). (text, size)."""
    for px in sizes:
        if _eyebrow_w(d, text, px) <= max_w:
            return text, px
    px, words = sizes[-1], " ".join((text or "").split()).split()
    while words and _eyebrow_w(d, " ".join(words) + "…", px) > max_w:
        words.pop()
    while words and words[-1].lower() in {"of", "at", "and", "&", "the", "for", "in", "to", "|", "-", "–"}:
        words.pop()
    return ((" ".join(words).rstrip(" ,;:-|") + "…") if words else ""), px


def _top_bar(img, d, t: dict, num: int, total: int, name: str, avatar):
    """Inner slides: a small name, the page count and a progress line, at the top, out of the dots' way."""
    _photo(img, avatar, name, M, 58 * S, 44 * S, t, ring=False)
    f = _body(24, semi=True)
    d.text((M + 60 * S, 58 * S + (44 * S - _lh(d, f)) // 2), C._fit_one_line(d, name, f, W // 2), font=f, fill=t["muted"])
    fc = _body(22, semi=True)
    ctr = f"{num} / {total}"
    d.text((W - M - C._tw(d, ctr, fc), 58 * S + (44 * S - _lh(d, fc)) // 2), ctr, font=fc, fill=t["muted"])
    d.rectangle([M, 128 * S, W - M, 130 * S], fill=t["rule"])
    d.rectangle([M, 128 * S, M + (W - 2 * M) * num // max(1, total), 130 * S], fill=t["accent"])


def _probe():
    """A 1x1 page to lay content out on without drawing it: only where it ends is wanted."""
    return ImageDraw.Draw(Image.new("RGB", (1, 1)))


def _scale_for(draw_fn, scales: tuple = SCALES) -> float:
    """The largest scale at which draw_fn's content fits above the safe bottom."""
    d = _probe()
    for k in scales:
        if draw_fn(None, d, TOP, k) <= SAFE:
            return k
    return scales[-1]


def _centred(img, draw_fn, scales: tuple = SCALES) -> Image.Image:
    """Lay a slide's content out between the top bar and the safe bottom, centred, at the largest
    scale that fits. draw_fn(layer, draw, y, k) draws from y at scale k and returns the y it reached;
    it is measured on a probe first, then drawn once on the page, moved down to centre it."""
    d = _probe()
    for k in scales:
        used = draw_fn(None, d, TOP, k)
        if used <= SAFE or k == scales[-1]:
            break
    draw_fn(img, ImageDraw.Draw(img), TOP + max(0, (SAFE - used) // 2), k)
    return img


# ── icons: simple line drawings, one per point ──────────────────────────────────────────────────
def _outline(d, pts: list, col, w: int):
    """A closed outline as one joined line: Pillow draws a polygon's wide outline through two
    page-sized images, 115 MB each time at this scale."""
    d.line(list(pts) + list(pts[:2]), fill=col, width=w, joint="curve")


def icon(d, kind: str, cx: int, cy: int, r: int, col, bg) -> bool:
    """Draw a named icon centred on (cx, cy), about 2r across. False if the name isn't one we draw."""
    w = max(2, int(r * 0.16))
    box = lambda k: [cx - r * k, cy - r * k, cx + r * k, cy + r * k]
    if kind == "pulse":
        d.ellipse(box(1), outline=col, width=w)
        pts = [(-.62, 0), (-.25, 0), (-.1, -.45), (.12, .45), (.3, 0), (.62, 0)]
        d.line([(int(cx + a * r), int(cy + b * r)) for a, b in pts], fill=col, width=w, joint="curve")
    elif kind == "alert":
        _outline(d, [(cx, cy - r), (cx + r, cy + r * .82), (cx - r, cy + r * .82)], col, w)
        d.line([(cx, cy - r * .32), (cx, cy + r * .24)], fill=col, width=w)
        d.ellipse([cx - w * .8, cy + r * .42, cx + w * .8, cy + r * .42 + w * 1.6], fill=col)
    elif kind == "network":
        a, b, c = (cx, cy - r * .7), (cx - r * .78, cy + r * .6), (cx + r * .78, cy + r * .6)
        for p, q in ((a, b), (a, c), (b, c)):
            d.line([p, q], fill=col, width=w)
        for x, y in (a, b, c):
            d.ellipse([x - r * .28, y - r * .28, x + r * .28, y + r * .28], fill=bg, outline=col, width=w)
    elif kind == "link":
        # two chain links on a slant, the second over the first
        rr = int(r * .36)
        for (x1, y1), (x2, y2) in (((-.62, .62), (-.16, .16)), ((.16, -.16), (.62, -.62))):
            ends = [(cx + x1 * r, cy + y1 * r), (cx + x2 * r, cy + y2 * r)]
            for c_, k in ((col, rr), (bg, rr - w)):
                d.line(ends, fill=c_, width=2 * k)
                for x, y in ends:
                    d.ellipse([x - k, y - k, x + k, y + k], fill=c_)
    elif kind in ("growth", "decline"):
        d.line([(cx - r, cy - r * .9), (cx - r, cy + r * .9), (cx + r, cy + r * .9)], fill=col, width=w)
        up = kind == "growth"
        pts = [(-.7, .45 if up else -.5), (-.2, 0 if up else -.05), (.15, .2 if up else .25), (.7, -.5 if up else .55)]
        pts = [(cx + a * r, cy + b * r) for a, b in pts]
        d.line(pts, fill=col, width=w, joint="curve")
        (px, py), (ex, ey) = pts[-2], pts[-1]
        ln = ((ex - px) ** 2 + (ey - py) ** 2) ** .5
        ux, uy = (ex - px) / ln, (ey - py) / ln
        tip = (ex + ux * r * .18, ey + uy * r * .18)
        d.polygon([tip, (ex - ux * r * .16 - uy * r * .2, ey - uy * r * .16 + ux * r * .2),
                   (ex - ux * r * .16 + uy * r * .2, ey - uy * r * .16 - ux * r * .2)], fill=col)
    elif kind == "money":
        d.rounded_rectangle([cx - r, cy - r * .6, cx + r, cy + r * .6], radius=int(r * .16), outline=col, width=w)
        d.ellipse(box(.32), outline=col, width=w)
        for sx in (-1, 1):
            d.ellipse([cx + sx * r * .66 - w, cy - w, cx + sx * r * .66 + w, cy + w], fill=col)
    elif kind == "clock":
        d.ellipse(box(1), outline=col, width=w)
        d.line([(cx, cy), (cx, cy - r * .58)], fill=col, width=w)
        d.line([(cx, cy), (cx + r * .42, cy + r * .2)], fill=col, width=w)
    elif kind == "people":
        for sx, k in ((-.44, .84), (.40, 1.0)):
            hx, hr = cx + sx * r, r * .27 * k
            hy = cy - r * .38 - (1 - k) * r * .1
            d.ellipse([hx - hr, hy - hr, hx + hr, hy + hr], outline=col, width=w)
            d.arc([hx - r * .5 * k, hy + hr + r * .12, hx + r * .5 * k, hy + hr + r * .12 + r * k], 180, 360, fill=col, width=w)
    elif kind == "target":
        for k in (1, .62):
            d.ellipse(box(k), outline=col, width=w)
        d.ellipse(box(.2), fill=col)
    elif kind == "check":
        d.ellipse(box(1), outline=col, width=w)
        d.line([(cx - r * .42, cy + r * .02), (cx - r * .1, cy + r * .34), (cx + r * .46, cy - r * .3)], fill=col, width=w, joint="curve")
    elif kind == "shield":
        _outline(d, [(cx, cy - r), (cx + r * .85, cy - r * .62), (cx + r * .7, cy + r * .4), (cx, cy + r),
                   (cx - r * .7, cy + r * .4), (cx - r * .85, cy - r * .62)], col, w)
        d.line([(cx - r * .32, cy), (cx - r * .06, cy + r * .28), (cx + r * .38, cy - r * .24)], fill=col, width=w, joint="curve")
    elif kind == "idea":
        d.ellipse([cx - r * .66, cy - r, cx + r * .66, cy + r * .32], outline=col, width=w)
        d.line([(cx - r * .3, cy + r * .55), (cx + r * .3, cy + r * .55)], fill=col, width=w)
        d.line([(cx - r * .22, cy + r * .85), (cx + r * .22, cy + r * .85)], fill=col, width=w)
    elif kind == "gear":
        import math
        pts = []
        for k in range(16):
            a = k * math.pi / 8
            rr = r if k % 2 == 0 else r * .74
            pts.append((cx + rr * math.cos(a), cy + rr * math.sin(a)))
        _outline(d, pts, col, w)
        d.ellipse(box(.3), outline=col, width=w)
    elif kind == "calendar":
        d.rounded_rectangle([cx - r, cy - r * .75, cx + r, cy + r * .9], radius=int(r * .14), outline=col, width=w)
        d.line([(cx - r, cy - r * .32), (cx + r, cy - r * .32)], fill=col, width=w)
        for sx in (-.5, .5):
            d.line([(cx + sx * r, cy - r), (cx + sx * r, cy - r * .55)], fill=col, width=w)
        for gx in (-.5, 0, .5):
            for gy in (.12, .52):
                d.ellipse([cx + gx * r - w, cy + gy * r - w, cx + gx * r + w, cy + gy * r + w], fill=col)
    elif kind == "chat":
        d.rounded_rectangle([cx - r, cy - r * .78, cx + r, cy + r * .5], radius=int(r * .3), outline=col, width=w)
        d.polygon([(cx - r * .45, cy + r * .48), (cx - r * .5, cy + r * .95), (cx - r * .05, cy + r * .48)], fill=col)
    elif kind == "search":
        d.ellipse([cx - r, cy - r, cx + r * .3, cy + r * .3], outline=col, width=w)
        d.line([(cx + r * .2, cy + r * .2), (cx + r, cy + r)], fill=col, width=int(w * 1.3))
    elif kind == "doc":
        _outline(d, [(cx - r * .7, cy - r), (cx + r * .3, cy - r), (cx + r * .7, cy - r * .6), (cx + r * .7, cy + r), (cx - r * .7, cy + r)], col, w)
        for k in (-.25, .15, .55):
            d.line([(cx - r * .38, cy + k * r), (cx + r * .38, cy + k * r)], fill=col, width=w)
    else:
        return False
    return True


def _tile(d, t: dict, kind: str, number: str, x: int, y: int, size: int):
    """A rounded tile with the point's icon, or its number when the writer named no icon we draw."""
    tile = _panel(t)
    d.rounded_rectangle([x, y, x + size, y + size], radius=int(size * 0.26), fill=tile)
    col = _icon_colour(t)
    if kind in ICONS and icon(d, kind, x + size // 2, y + size // 2, int(size * 0.3), col, tile):
        return
    f = _display(size / S * 0.5, t)
    b = d.textbbox((0, 0), number, font=f)
    d.text((x + (size - (b[2] - b[0])) // 2 - b[0], y + (size - (b[3] - b[1])) // 2 - b[1]), number, font=f, fill=col)


# ── carousel slides ──────────────────────────────────────────────────────────────────────────────
def _short_title(title: str) -> str:
    """The first part of a long title ("Revenue Growth Coach · Founder, CUSP" → "Revenue Growth
    Coach"): the whole line is too small to read once a phone shrinks the slide."""
    return re.split(r"\s+[·|•–—]\s+|\s*\|\s*", (title or "").strip())[0].strip()


def cover(t: dict, headline: str, kicker: str, sub: str, audience: str, promise: str,
          name: str, title: str, avatar, total: int, tiles: list | None = None) -> Image.Image:
    """The slide the feed shows. The promise as big as it fits, what's inside (the points' icons
    and how long it takes), the author's face and a swipe cue, all above LinkedIn's page dots."""
    img, d = _page(t)
    mw = W - 2 * M
    lw = _logo(img, t, W - M, M, 50 * S)
    eb = f"For {audience}" if audience else ""
    if eb:
        eb, px = _fit_eyebrow(d, eb, mw - lw - 40 * S)
        _eyebrow(d, eb, M, M + int((8 + (30 - px) / 2) * S), t["accent"], px)
    # the bottom: author left, swipe right, clear of the dots
    ph = 112 * S
    author_y = SAFE - 16 * S - ph
    fp = _body(32, bold=True)
    cue = "Swipe  →"
    bw, bh = C._tw(d, cue, fp) + 60 * S, 78 * S
    by = author_y + (ph - bh) // 2
    d.rounded_rectangle([W - M - bw, by, W - M, by + bh], radius=bh // 2, fill=t["accent"])
    d.text((W - M - bw + 32 * S, by + (bh - _lh(d, fp)) // 2 - 2 * S), cue, font=fp, fill=_on(t["accent"], t))
    _author(img, d, t, avatar, name, _short_title(title), M, author_y, ph, 36, 25, W - 2 * M - ph - bw - 60 * S)
    # what's inside: a tile per point with its icon, then the count and reading time
    icons = [k for k, _ in (tiles or []) if k in ICONS]
    show_row = 3 <= len(icons) == len(tiles or []) <= 6
    fpr = _body(30, semi=True)
    tile = 88 * S
    inside_h = 0
    if show_row or promise:
        inside_h = (tile if show_row else _lh(d, fpr)) + 56 * S
    bottom = author_y - inside_h
    # the headline, as big as fits with the subline above the inside row
    top = M + (96 * S if eb or lw else 30 * S)
    full = (headline + " " + kicker).strip() if kicker and kicker.lower() not in headline.lower() else headline
    fs = _fit(d, sub, (42, 39, 36), _body, mw, 3) if sub else None
    hs = (_block_h(d, sub, fs, mw, 10 * S) + 36 * S) if sub else 0
    f = None
    for z in (132, 124, 116, 108, 100, 94, 88, 82, 76, 70, 64):
        cand = _display(z, t)
        lines = _wrap(d, full, cand, mw, kicker)
        if len(lines) <= 5 and all(_line_w(d, ln, cand) <= mw for ln in lines) and not _ragged(lines) \
                and top + _block_h(d, full, cand, mw, 10 * S, kicker) + hs <= bottom - 30 * S:
            f = cand
            break
    f = f or _display(64, t)
    y = _write(d, full, f, M, top, mw, t["ink"], 10 * S, kicker, t["accent"]) + 26 * S
    if sub:
        y = _write(d, sub, fs, M, y, mw, t["body"], 10 * S) + 30 * S
    if inside_h:
        ry = min(bottom, y + 40 * S)   # under the words; the space left over sits above the author
        x = M
        if show_row:
            for k in icons:
                _tile(d, t, k, "", x, ry, tile)
                x += tile + 14 * S
            x += 14 * S
        if promise:
            ty = ry + ((tile if show_row else _lh(d, fpr)) - _lh(d, fpr)) // 2
            d.text((x, ty), C._fit_one_line(d, promise, fpr, W - M - x), font=fpr, fill=t["muted"])
    return _done(img)


def _point_content(t: dict, label: str, title: str, body: str, action: str, kind: str, step: int, source: str = ""):
    """A point slide's content, drawn from y at scale k (see _centred)."""
    mw = W - 2 * M
    act_label = (t.get("action_label") or "Try this").strip()

    def content(layer, ld, y, k):
        y = _eyebrow(ld, label or f"Step {step}", M, y, t["accent"]) + int(34 * S * k)
        size = int(128 * S * k)
        _tile(ld, t, kind, str(step), M, y, size)
        y += size + int(46 * S * k)
        ft = _fit(ld, title, [z * k for z in (84, 78, 72, 66, 60)], lambda z: _display(z, t), mw, 3, even=True)
        y = _write(ld, title, ft, M, y, mw, t["ink"], int(10 * S * k)) + int(22 * S * k)
        if body:
            fb = _fit(ld, body, [z * k for z in (44, 41, 38)], _body, mw, 6)
            y = _write(ld, body, fb, M, y, mw, t["body"], int(10 * S * k)) + int(30 * S * k)
        if action:
            fa = _fit(ld, action, [z * k for z in (42, 39, 36)], lambda z: _body(z, bold=True), mw - 64 * S, 3)
            h = _block_h(ld, action, fa, mw - 64 * S, int(10 * S * k)) + int(90 * S * k)
            ld.rounded_rectangle([M, y, W - M, y + h], radius=26 * S, fill=C._mix(t["bg"], t["accent"], 0.13))
            _eyebrow(ld, act_label, M + 32 * S, y + int(24 * S * k), t["accent"], 22 * k)
            _write(ld, action, fa, M + 32 * S, y + int(58 * S * k), mw - 64 * S, t["ink"], int(10 * S * k))
            y += h + int(24 * S * k)
        if source:
            ld.text((M, y), C._fit_one_line(ld, f"Source: {source}", _body(22 * k), mw), font=_body(22 * k), fill=t["muted"])
            y += _lh(ld, _body(22 * k))
        return y

    return content


def point(t: dict, num: int, total: int, name: str, avatar, label: str, title: str, body: str,
          action: str, kind: str, step: int, source: str = "", scales: tuple = SCALES) -> Image.Image:
    img, d = _page(t)
    _top_bar(img, d, t, num, total, name, avatar)
    return _done(_centred(img, _point_content(t, label, title, body, action, kind, step, source), scales))


def stat(t: dict, num: int, total: int, name: str, avatar, label: str, number: str, title: str,
         body: str, source: str = "") -> Image.Image:
    t = _loud(t)
    img, d = _page(t)
    _top_bar(img, d, t, num, total, name, avatar)
    mw = W - 2 * M

    def content(layer, ld, y, k):
        y = _eyebrow(ld, label or "The number", M, y, t["accent"]) + int(36 * S * k)
        sizes = [z * k for z in (260, 230, 200, 170, 140, 120) if C._tw(ld, number, _display(z * k, t)) <= mw] or [120 * k]
        fs = _display(sizes[0], t)
        ld.text((M - 6 * S, y), number, font=fs, fill=t["accent"])
        y += int(_lh(ld, fs) * 1.08)
        ft = _fit(ld, title, [z * k for z in (66, 60, 56, 52)], lambda z: _display(z, t), mw, 3)
        y = _write(ld, title, ft, M, y, mw, t["ink"], int(10 * S * k)) + int(30 * S * k)
        if body:
            fb = _fit(ld, body, [z * k for z in (42, 39, 36)], _body, mw, 5)
            y = _write(ld, body, fb, M, y, mw, t["body"], int(12 * S * k)) + int(30 * S * k)
        if source:
            ld.text((M, y), C._fit_one_line(ld, f"Source: {source}", _body(22 * k), mw), font=_body(22 * k), fill=t["muted"])
            y += _lh(ld, _body(22 * k))
        return y

    return _done(_centred(img, content))


def overview(t: dict, num: int, total: int, name: str, avatar, heading: str, tiles: list) -> Image.Image:
    """The deck's points at a glance: a tile each, its icon (or number) and its short tag."""
    img, d = _page(t)
    _top_bar(img, d, t, num, total, name, avatar)
    mw = W - 2 * M

    def content(layer, ld, y, k):
        y = _eyebrow(ld, "At a glance", M, y, t["accent"]) + int(40 * S * k)
        fh = _fit(ld, heading, [z * k for z in (80, 74, 68, 62)], lambda z: _display(z, t), mw, 3)
        y = _write(ld, heading, fh, M, y, mw, t["ink"], int(12 * S * k)) + int(56 * S * k)
        gap = int(26 * S * k)
        cols = 2
        tw = (mw - gap) // cols
        th = int(190 * S * k)
        fl = _fit(ld, max((tg for _, tg in tiles), key=len), [z * k for z in (34, 31, 28, 25)], lambda z: _body(z, bold=True),
                  tw - int(170 * S * k), 2)
        for i, (kind, tag) in enumerate(tiles):
            x = M + (i % cols) * (tw + gap)
            ty = y + (i // cols) * (th + gap)
            ld.rounded_rectangle([x, ty, x + tw, ty + th], radius=int(28 * S * k), fill=_panel(t))
            ic = int(44 * S * k)
            if not (kind in ICONS and icon(ld, kind, x + int(86 * S * k), ty + th // 2, ic, _icon_colour(t), _panel(t))):
                fn = _display(62 * k, t)
                ld.text((x + int(56 * S * k), ty + (th - _lh(ld, fn)) // 2), str(i + 1), font=fn, fill=_icon_colour(t))
            tx = x + int(156 * S * k)
            ink = t["bg"] if _light(t) else t["ink"]
            hb = _block_h(ld, tag, fl, tw - (tx - x) - int(20 * S * k), int(6 * S * k))
            _write(ld, tag, fl, tx, ty + (th - hb) // 2, tw - (tx - x) - int(20 * S * k), ink, int(6 * S * k))
        rows = (len(tiles) + cols - 1) // cols
        return y + rows * th + (rows - 1) * gap

    return _done(_centred(img, content))


def recap(t: dict, num: int, total: int, name: str, avatar, title: str, bullets: list) -> Image.Image:
    img, d = _page(t)
    _top_bar(img, d, t, num, total, name, avatar)
    mw = W - 2 * M
    items = [str(b).strip() for b in (bullets or []) if str(b).strip()][:5]

    def content(layer, ld, y, k):
        y = _eyebrow(ld, "Recap", M, y, t["accent"]) + int(40 * S * k)
        ft = _fit(ld, title, [z * k for z in (84, 78, 72, 66)], lambda z: _display(z, t), mw, 2)
        y = _write(ld, title, ft, M, y, mw, t["ink"], int(12 * S * k)) + int(50 * S * k)
        r = int(28 * S * k)
        tx = M + 2 * r + int(30 * S * k)
        fi = _fit(ld, max(items, key=len) if items else "", [z * k for z in (40, 37, 34)], lambda z: _body(z, semi=True), W - M - tx, 3)
        for it in items:
            cy = y + _lh(ld, fi) // 2
            ld.ellipse([M, cy - r, M + 2 * r, cy + r], fill=t["accent"])
            ld.line([(M + r * .55, cy + r * .02), (M + r * .9, cy + r * .38), (M + r * 1.48, cy - r * .32)],
                    fill=_on(t["accent"], t), width=max(3, r // 6), joint="curve")
            y = _write(ld, it, fi, tx, y, W - M - tx, t["ink"], int(8 * S * k)) + int(34 * S * k)
        return y

    return _done(_centred(img, content))


def last(t: dict, headline: str, kicker: str, cta: str, name: str, title: str, avatar) -> Image.Image:
    t = _loud(t)
    img, d = _page(t)
    _logo(img, t, W - M, M, 50 * S)
    y = _eyebrow(d, "The takeaway", M, M + 10 * S, t["accent"]) + 110 * S
    mw = W - 2 * M
    full = (headline + " " + kicker).strip() if kicker and kicker.lower() not in headline.lower() else headline
    author_y = H - M - 170 * S
    f = _fit(d, full, (112, 104, 96, 90, 84, 78, 72, 66, 60), lambda z: _display(z, t), mw, 5, kicker, even=True)
    y = _write(d, full, f, M, y, mw, t["ink"], 12 * S, kicker, t["accent"]) + 64 * S
    if cta:
        fc = _fit(d, cta, (40, 37, 34, 31), lambda z: _body(z, bold=True), mw - 80 * S, 3)
        h = _block_h(d, cta, fc, mw - 80 * S, 10 * S) + 70 * S
        if y + h < author_y - 30 * S:
            d.rounded_rectangle([M, y, W - M, y + h], radius=26 * S, fill=t["accent"])
            _write(d, cta, fc, M + 40 * S, y + 35 * S, mw - 80 * S, _on(t["accent"], t), 10 * S)
    _author(img, d, t, avatar, name, _short_title(title), M, author_y, 110 * S, 34, 24, W - 2 * M - 130 * S)
    return _done(img)


def _singular(unit: str) -> str:
    """'fixes' → 'fix', 'stories' → 'story', 'habits' → 'habit': to find the unit in each slide's label."""
    if unit.endswith("ies"):
        return unit[:-3] + "y"
    if unit.endswith(("xes", "ches", "shes", "sses", "zes")):
        return unit[:-2]
    return unit[:-1] if unit.endswith("s") else unit


def carousel_slides(content: dict, t: dict, t_cover: dict, name: str, title: str, avatar, promise: str = "") -> list:
    """The whole deck in the v2 design, from the writer's deck (carousel.py's schema)."""
    hook, ctx = content["hook_slide"], content.get("context_slide")
    c_slides, rcp, cta = content.get("content_slides", []), content.get("recap_slide"), content["cta_slide"]
    points = [s for s in c_slides if not (s.get("kind") == "stat" and s.get("stat"))]
    tiles = [((s.get("icon") or "").strip().lower(), (s.get("tag") or "").strip()) for s in points]
    said = re.search(r"\b([2-9]|10)\b", hook.get("headline", ""))
    if said and int(said.group(1)) != len(points):
        tiles = []
    show_overview = 3 <= len(points) <= 6 and bool(tiles) and all(tg for _, tg in tiles)
    # "The 5 fixes" only when every point is one: a deck of 2 traps and 3 fixes was counted as 5 fixes
    unit = (hook.get("unit") or "").strip()
    one = _singular(unit)
    counted = bool(one) and all(one.lower() in (s.get("label") or "").lower() for s in points)
    if unit and not counted and promise.startswith(f"{len(points)} {unit} · "):
        promise = promise[len(f"{len(points)} {unit} · "):]
    total = 1 + (1 if ctx else 0) + (1 if show_overview else 0) + len(c_slides) + (1 if rcp else 0) + 1
    base, kicker = C._split_hook_pill(hook.get("headline", ""))
    slides = [cover(t_cover, base or hook.get("headline", ""), kicker, hook.get("subtext", ""), hook.get("audience", ""),
                    promise, name, title, avatar, total, tiles)]
    num = 2
    if ctx:
        slides.append(_context(t, num, total, name, avatar, ctx.get("title", ""), ctx.get("body", "")))
        num += 1
    if show_overview:
        heading = f"The {len(points)} {unit}" if counted else "In this deck"
        slides.append(overview(t, num, total, name, avatar, heading, tiles))
        num += 1
    # one size for every point slide, the size the fullest one fits at: sizes jumping from slide to
    # slide looked careless
    pts_k = min([_scale_for(_point_content(t, s.get("label", ""), s.get("title", ""), s.get("body", ""), s.get("action", ""),
                                           (s.get("icon") or "").strip().lower(), i + 1, s.get("source", "")))
                 for i, s in enumerate(points)] or [1.0])
    pts_scales = tuple(k for k in SCALES if k <= pts_k)
    step = 0
    for s in c_slides:
        if s.get("kind") == "stat" and s.get("stat"):
            slides.append(stat(t, num, total, name, avatar, s.get("label", ""), s["stat"], s.get("title", ""),
                               s.get("body", ""), s.get("source", "")))
        else:
            step += 1
            slides.append(point(t, num, total, name, avatar, s.get("label", ""), s.get("title", ""), s.get("body", ""),
                                s.get("action", ""), (s.get("icon") or "").strip().lower(), step, s.get("source", ""),
                                pts_scales))
        num += 1
    if rcp:
        slides.append(recap(t, num, total, name, avatar, rcp.get("title", ""), rcp.get("bullets", [])))
        num += 1
    cbase, ckicker = C._split_hook_pill(cta.get("headline", ""))
    slides.append(last(t, cbase or cta.get("headline", ""), ckicker, cta.get("cta", ""), name, title, avatar))
    return slides


def _context(t: dict, num: int, total: int, name: str, avatar, title: str, body: str) -> Image.Image:
    """Slide 2: why keep swiping. Words only, set large, with an accent rule between them."""
    img, d = _page(t)
    _top_bar(img, d, t, num, total, name, avatar)
    mw = W - 2 * M

    def content(layer, ld, y, k):
        y = _eyebrow(ld, "Start here", M, y, t["accent"]) + int(44 * S * k)
        ft = _fit(ld, title, [z * k for z in (88, 82, 76, 70, 64)], lambda z: _display(z, t), mw, 4)
        y = _write(ld, title, ft, M, y, mw, t["ink"], int(12 * S * k)) + int(40 * S * k)
        ld.rectangle([M, y, M + int(110 * S * k), y + int(10 * S * k)], fill=t["accent"])
        y += int(56 * S * k)
        if body:
            fb = _fit(ld, body, [z * k for z in (44, 41, 38)], _body, mw, 7)
            y = _write(ld, body, fb, M, y, mw, t["body"], int(12 * S * k))
        return y

    return _done(_centred(img, content))


# ── single image cards ───────────────────────────────────────────────────────────────────────────
def _quote_mark(d, x: int, y: int, size: int, col):
    """A drawn “: two round heads, each with a curved tail."""
    r = size // 4
    for k in range(2):
        cx, cy = x + r + k * (2 * r + size // 7), y + size - r
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
        d.polygon([(cx - r, cy), (cx - r * 0.1, cy - int(r * 2.6)), (cx + r * 0.75, cy - int(r * 2.2)), (cx + r * 0.2, cy - r * 0.2)], fill=col)


def quote_card(t: dict, line: str, emph: str, sub: str, tag: str, name: str, title: str, avatar) -> Image.Image:
    img, d = _page(t)
    mw = W - 2 * M
    foot = H - M - 120 * S
    top = M + (_lh(d, _body(23, semi=True)) + 40 * S if tag else 0)
    if tag:
        _eyebrow(d, tag, M, M, t["accent"])
    fs = _fit(d, sub, (36, 33, 30), _body, mw, 3) if sub else None
    hs = (_block_h(d, sub, fs, mw, 10 * S) + 40 * S) if sub else 0
    mark = 150 * S
    # the largest size that fits the width AND the height above the signature: a nine-word line
    # set at the widest size ran down into the author's name
    room = foot - 40 * S - top - mark - 50 * S - hs
    f = None
    for z in (150, 136, 124, 112, 104, 96, 88, 80, 72, 64, 58):
        cand = _display(z, t)
        lines = _wrap(d, line, cand, mw, emph)
        if all(_line_w(d, ln, cand) <= mw for ln in lines) and not _ragged(lines) \
                and _block_h(d, line, cand, mw, 18 * S, emph) <= room:
            f = cand
            break
    if f is None:   # very long: the smallest size, and a smaller quote mark to make room
        f, mark = _display(58, t), 96 * S
    hq = _block_h(d, line, f, mw, 18 * S, emph)
    block = mark + 50 * S + hq + hs
    y = top + max(0, (foot - 40 * S - top - block) // 2)
    _quote_mark(d, M, y, mark, t["accent"])
    y = _write(d, line, f, M, y + mark + 50 * S, mw, t["ink"], 18 * S, emph, t["accent"])
    if sub:
        _write(d, sub, fs, M, y + 40 * S - 18 * S, mw, t["body"], 10 * S)
    d.rectangle([M, foot, W - M, foot + 2 * S], fill=t["rule"])
    lw = _logo(img, t, W - M, H - M - 60 * S, 40 * S)
    _author(img, d, t, avatar, name, _short_title(title), M, H - M - 82 * S, 76 * S, 28, 22, W - 2 * M - 100 * S - lw)
    return _done(img)


def list_card(t: dict, tag: str, headline: str, emph: str, points: list, icons: list, name: str, title: str, avatar) -> Image.Image:
    img, d = _page(t)
    mw = W - 2 * M
    y = (_eyebrow(d, tag, M, M, t["accent"]) + 26 * S) if tag else M
    f = _fit(d, headline, (82, 76, 70, 64, 58, 52), lambda z: _display(z, t), mw, 3, emph)
    y = _write(d, headline, f, M, y, mw, t["ink"], 10 * S, emph, t["accent"]) + 46 * S
    foot = H - M - 100 * S
    pts = [p for p in points if p][:5]
    rows = max(1, len(pts))
    rh = (foot - 40 * S - y) // rows
    tile = min(int(rh * 0.82), 108 * S)
    tx = M + tile + 34 * S
    tw = W - M - tx
    fl = _body(24, semi=True)
    fp = _fit(d, max(pts, key=len) if pts else "", (42, 39, 36, 33, 30), lambda z: _body(z, bold=True), tw, 2)
    for i, p in enumerate(pts):
        cy = y + rh // 2
        _tile(d, t, (icons[i] if i < len(icons) else "") or "", str(i + 1), M, cy - tile // 2, tile)
        hb = _block_h(d, p, fp, tw, 6 * S)
        g = _lh(d, fl) + 8 * S + hb
        top = cy - g // 2
        d.text((tx, top), f"0{i + 1}" if len(pts) < 10 else str(i + 1), font=fl, fill=t["accent"])
        _write(d, p, fp, tx, top + _lh(d, fl) + 8 * S, tw, t["ink"], 6 * S)
        y += rh
    lw = _logo(img, t, W - M, H - M - 52 * S, 36 * S)
    _author(img, d, t, avatar, name, _short_title(title), M, H - M - 72 * S, 64 * S, 26, 21, W - 2 * M - 90 * S - lw)
    return _done(img)


def points_from(points: list) -> list:
    """'1. Label: rest' and the like, as the short labels a list card shows."""
    out = []
    for pt in points or []:
        s = re.sub(r"^\s*(?:\d{1,2}\s*[.):-]|[-•*→✓])\s*", "", str(pt)).strip()
        if s:
            out.append(s)
    return out
