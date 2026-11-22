"""Voyce's designed cards and carousel slides, v2 (Nov 2026).

Built from what LinkedIn document and image posts that get read and saved have in common: a cover that
makes a specific promise in a few words, with the author's face and a swipe cue; inner slides of one
idea and few words, set large enough to read when a phone shows the slide at a third of its size; one
visual anchor per slide (a number, an icon, a grid of tiles); the logo on the first and last slide only;
the bottom strip left clear for LinkedIn's page dots; one call to action. Colours come from the look
carousel.py picks (a brand kit look or one of its colour sets). carousel.py calls these and goes back
to its earlier design if one of them raises, so a post never fails on a layout.
"""
import os
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


# A cover that opens on a number ("5 controls…", "₹2.4 Cr…", "3x…", "40%…"): the number is drawn huge (cover())
_COVER_NUM = re.compile(r"^((?:₹|\$|€)?\d[\d,.]*\s?(?:%|x|×|k|K|Cr|L|M|B)?)\s+(\S.*)$")
COVER_BIG_NUMBER = os.getenv("COVER_BIG_NUMBER", "1") != "0"


def _pair_rows(d, t: dict, wrong: str, right: str, y: int, draw: bool = True) -> int:
    """The crossed-out and the ticked line, compact, for the cover's preview; their height."""
    mw = W - 2 * M
    r, pad = 17 * S, 24 * S
    tw = mw - 2 * pad - 2 * r - 22 * S
    f = _fit(d, max((wrong, right), key=len), (36, 34, 32, 30), lambda z: _body(z, bold=True), tw, 2)
    y0 = y
    for ok, text in ((False, wrong), (True, right)):
        hb = _block_h(d, text, f, tw, 6 * S)
        h = max(hb, 2 * r) + 2 * pad
        if draw:
            d.rounded_rectangle([M, y, W - M, y + h], radius=22 * S,
                                fill=C._mix(t["bg"], t["accent"], 0.16) if ok else C._mix(t["bg"], t["ink"], 0.06))
            cx, cy = M + pad + r, y + h // 2
            _mark(d, ok, cx, cy, r, t["accent"] if ok else C._mix(t["bg"], t["ink"], 0.35), _on(t["accent"], t) if ok else t["bg"])
            _write(d, text, f, cx + r + 22 * S, y + (h - hb) // 2, tw, t["ink"] if ok else t["muted"], 6 * S)
        y += h + 12 * S
    return y - y0 - 12 * S


def cover(t: dict, headline: str, kicker: str, sub: str, audience: str, promise: str,
          name: str, title: str, avatar, total: int, tiles: list | None = None, pair: tuple | None = None) -> Image.Image:
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
    ph = 132 * S      # the face reads at feed size (22 Nov 2026 carousel audit: a face lifts a deck's reach)
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
    # the difference the deck is about, as a preview above the author (the top decks show it on the cover;
    # a cover with a visual drew 1.6x the clicks of a text-only one, van der Blom 2025)
    pair_h = _pair_rows(d, t, pair[0], pair[1], 0, draw=False) if pair and pair[0] and pair[1] else 0
    if pair_h:
        show_row, promise = False, ""
        inside_h = pair_h + 50 * S
    bottom = author_y - inside_h
    # the headline, as big as fits with the subline above the inside row
    top = M + (96 * S if eb or lw else 30 * S)
    full = (headline + " " + kicker).strip() if kicker and kicker.lower() not in headline.lower() else headline
    fs = _fit(d, sub, (42, 39, 36), _body, mw, 3) if sub else None
    hs = (_block_h(d, sub, fs, mw, 10 * S) + 36 * S) if sub else 0
    y = None
    m = _COVER_NUM.match(full) if COVER_BIG_NUMBER else None
    if m and not re.fullmatch(r"(19|20)\d\d", m.group(1).strip()):
        # The number as the cover's hero, huge in the accent, the rest of the headline under it (top-posts research,
        # 22 Nov 2026: the strongest covers set the number big, as Ruben Hassid's do, not inside a line of words).
        # Measured first; drawn only when the rest of the headline fits under it, else the usual cover below.
        num, rest = m.group(1).strip(), m.group(2).strip()
        fnum = next((_display(z, t) for z in (440, 400, 360, 320, 280, 240, 200)
                     if C._tw(d, num, _display(z, t)) <= mw), None)
        if fnum is not None:
            nb = d.textbbox((0, 0), num, font=fnum)
            ry = top + (nb[3] - nb[1]) + 40 * S
            fr = None
            for z in (100, 94, 88, 82, 76, 70, 64, 58):
                cand = _display(z, t)
                lines = _wrap(d, rest, cand, mw)
                if len(lines) <= 4 and all(_line_w(d, ln, cand) <= mw for ln in lines) and not _ragged(lines) \
                        and ry + _block_h(d, rest, cand, mw, 10 * S) + hs <= bottom - 30 * S:
                    fr = cand
                    break
            if fr is not None:
                d.text((M - nb[0] - 4 * S, top - nb[1]), num, font=fnum, fill=t["accent"])
                y = _write(d, rest, fr, M, ry, mw, t["ink"], 10 * S) + 26 * S
    if y is None:
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
    if pair_h:
        _pair_rows(d, t, pair[0], pair[1], author_y - 34 * S - pair_h)
    elif inside_h:
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


def _mark(ld, ok: bool, cx: int, cy: int, r: int, fill, ink):
    """A tick or a cross in a filled circle."""
    ld.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
    w = max(3, r // 4)
    if ok:
        ld.line([(cx - r * .45, cy + r * .02), (cx - r * .1, cy + r * .38), (cx + r * .48, cy - r * .32)], fill=ink, width=w, joint="curve")
    else:
        ld.line([(cx - r * .38, cy - r * .38), (cx + r * .38, cy + r * .38)], fill=ink, width=w)
        ld.line([(cx - r * .38, cy + r * .38), (cx + r * .38, cy - r * .38)], fill=ink, width=w)


_NUMBERED_LABEL = re.compile(r"^[A-Za-z]+[\s#.]*\d{1,2}$")   # "STEP 2", "MISTAKE 3", "No. 4"


def _point_content(t: dict, label: str, title: str, body: str, action: str, kind: str, step: int, source: str = "",
                   wrong: str = "", right: str = ""):
    """A point slide's content, drawn from y at scale k (see _centred). With wrong/right the difference IS the slide
    (22 Nov 2026 carousel audit: decks where every slide shows a difference ran ~2.5x their creators' median, "N tips"
    decks 0.2-0.8x): the step as a small label, the claim, then what most do and what works as two big cards filling
    most of the slide, the evidence under them. The faded "01" went: with the counter and the progress bar it was a
    third progress cue."""
    mw = W - 2 * M
    act_label = (t.get("action_label") or "Try this").strip()
    proof = bool(wrong and right)

    def content(layer, ld, y, k):
        if proof:
            lab = label or (f"Step {step}" if step else "")
            if lab:
                y = _eyebrow(ld, lab, M, y, t["accent"], 26 * k) + int(28 * S * k)
            ft = _fit(ld, title, [z * k for z in (78, 72, 66, 62, 58)], lambda z: _display(z, t), mw, 3, even=True)
            y = _write(ld, title, ft, M, y, mw, t["ink"], int(10 * S * k)) + int(42 * S * k)
            pad = int(38 * S * k)
            tw = mw - 2 * pad
            fr = _fit(ld, max((wrong, right), key=len), [z * k for z in (56, 52, 48, 45, 42)],
                      lambda z: _body(z, bold=True), tw, 3)
            r = int(19 * S * k)
            fe = 24 * k
            eh = max(_lh(ld, _body(fe, semi=True)), 2 * r)
            for ok, head, text in ((False, t.get("wrong_head") or "Most teams", wrong),
                                   (True, t.get("right_head") or "Do this", right)):
                hb = _block_h(ld, text, fr, tw, int(8 * S * k))
                h = pad + eh + int(20 * S * k) + hb + pad
                fill = C._mix(t["bg"], t["accent"], 0.16) if ok else C._mix(t["bg"], t["ink"], 0.06)
                ld.rounded_rectangle([M, y, W - M, y + h], radius=28 * S, fill=fill)
                cx, cy = M + pad + r, y + pad + eh // 2
                _mark(ld, ok, cx, cy, r, t["accent"] if ok else C._mix(t["bg"], t["ink"], 0.35),
                      _on(t["accent"], t) if ok else t["bg"])
                fh = _body(fe, semi=True)
                _eyebrow(ld, head, cx + r + int(18 * S * k), cy - _lh(ld, fh) // 2, t["accent"] if ok else t["muted"], fe)
                _write(ld, text, fr, M + pad, y + pad + eh + int(20 * S * k), tw, t["ink"] if ok else t["muted"],
                       int(8 * S * k))
                y += h + int(18 * S * k)
            if body:
                fb = _fit(ld, body, [z * k for z in (38, 36, 34, 32)], _body, mw, 3)
                y = _write(ld, body, fb, M, y + int(16 * S * k), mw, t["body"], int(8 * S * k)) + int(18 * S * k)
            if source:
                fs = _body(26 * k)
                ld.text((M, y), C._fit_one_line(ld, f"Source: {source}", fs, mw), font=fs, fill=t["muted"])
                y += _lh(ld, fs)
            return y
        y = _eyebrow(ld, label or (f"Step {step}" if step else ""), M, y, t["accent"]) + int(34 * S * k)
        if step or kind in ICONS:
            size = int(128 * S * k)
            _tile(ld, t, kind, str(step) if step else "", M, y, size)
            y += size + int(46 * S * k)
        ft = _fit(ld, title, [z * k for z in (84, 78, 72, 66, 60)], lambda z: _display(z, t), mw, 3, even=True)
        y = _write(ld, title, ft, M, y, mw, t["ink"], int(10 * S * k)) + int(22 * S * k)
        if body:
            fb = _fit(ld, body, [z * k for z in (44, 41, 38)], _body, mw, 6)
            y = _write(ld, body, fb, M, y, mw, t["body"], int(10 * S * k)) + int(36 * S * k)
        if action:
            fa = _fit(ld, action, [z * k for z in (42, 39, 36)], lambda z: _body(z, bold=True), mw - 64 * S, 3)
            h = _block_h(ld, action, fa, mw - 64 * S, int(10 * S * k)) + int(90 * S * k)
            ld.rounded_rectangle([M, y, W - M, y + h], radius=26 * S, fill=C._mix(t["bg"], t["accent"], 0.13))
            _eyebrow(ld, act_label, M + 32 * S, y + int(24 * S * k), t["accent"], 22 * k)
            _write(ld, action, fa, M + 32 * S, y + int(58 * S * k), mw - 64 * S, t["ink"], int(10 * S * k))
            y += h + int(24 * S * k)
        if source:
            fs = _body(26 * k)
            ld.text((M, y), C._fit_one_line(ld, f"Source: {source}", fs, mw), font=fs, fill=t["muted"])
            y += _lh(ld, fs)
        return y

    return content


def point(t: dict, num: int, total: int, name: str, avatar, label: str, title: str, body: str,
          action: str, kind: str, step: int, source: str = "", scales: tuple = SCALES,
          wrong: str = "", right: str = "") -> Image.Image:
    img, d = _page(t)
    _top_bar(img, d, t, num, total, name, avatar)
    return _done(_centred(img, _point_content(t, label, title, body, action, kind, step, source, wrong, right), scales))


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


def last(t: dict, headline: str, kicker: str, cta: str, name: str, title: str, avatar, follow: str = "") -> Image.Image:
    t = _loud(t)
    img, d = _page(t)
    _logo(img, t, W - M, M, 50 * S)
    y = _eyebrow(d, "The takeaway", M, M + 10 * S, t["accent"]) + 110 * S
    mw = W - 2 * M
    full = (headline + " " + kicker).strip() if kicker and kicker.lower() not in headline.lower() else headline
    # The author's face, big, next to the one ask (round 2 research: the best decks end on the person; a face lifted
    # reach 80-90% in van der Blom's 2022 study). Only a real photo is shown big; an initial stays small.
    face = 270 * S if avatar is not None else 110 * S     # ~20% of the slide (22 Nov 2026 carousel audit)
    author_y = H - M - face - 40 * S
    # The ask's room is kept first and the takeaway sized into what's left: a long takeaway used to push the
    # ask off the slide without a word (Thahseen's deck, 22 Nov 2026 audit, round 2)
    h = 0
    if cta:
        fc = _fit(d, cta, (40, 37, 34, 31), lambda z: _body(z, bold=True), mw - 80 * S, 3)
        h = _block_h(d, cta, fc, mw - 80 * S, 10 * S) + 70 * S
    room = author_y - 30 * S - y - ((h + 64 * S) if cta else 0)
    sizes = (112, 104, 96, 90, 84, 78, 72, 66, 60, 56, 52)
    f = None
    for i in range(len(sizes)):
        cand = _fit(d, full, sizes[i:], lambda z: _display(z, t), mw, 6, kicker, even=True)
        if _block_h(d, full, cand, mw, 12 * S, kicker) <= room:
            f = cand
            break
    f = f or _display(52, t)
    y = _write(d, full, f, M, y, mw, t["ink"], 12 * S, kicker, t["accent"]) + 64 * S
    if cta:
        if y + h < author_y - 20 * S:
            d.rounded_rectangle([M, y, W - M, y + h], radius=26 * S, fill=t["accent"])
            _write(d, cta, fc, M + 40 * S, y + 35 * S, mw - 80 * S, _on(t["accent"], t), 10 * S)
    if follow:   # who to follow for what, beside the face: the end slide's one ask after the save
        _photo(img, avatar, name, M, author_y, face, t)
        tx = M + face + 30 * S
        mwx = W - M - tx
        fn = _body(44 if avatar is not None else 34, bold=True)
        ff = _fit(d, follow, (32, 30, 28, 26), lambda z: _body(z, semi=True), mwx, 3)
        hf = _block_h(d, follow, ff, mwx, 6 * S)
        ty = author_y + (face - _lh(d, fn) - 12 * S - hf) // 2
        d.text((tx, ty), C._fit_one_line(d, name, fn, mwx), font=fn, fill=t["ink"])
        _write(d, follow, ff, tx, ty + _lh(d, fn) + 12 * S, mwx, t["accent"], 6 * S)
    elif avatar is not None:
        _author(img, d, t, avatar, name, _short_title(title), M, author_y, face, 44, 28, W - 2 * M - face - 40 * S)
    else:
        _author(img, d, t, avatar, name, _short_title(title), M, author_y, face, 34, 24, W - 2 * M - 130 * S)
    return _done(img)


def worksheet(t: dict, num: int, total: int, name: str, avatar, title: str, prompts: list, example: str = "") -> Image.Image:
    """The deck's method as a sheet to fill in: each prompt numbered, with lines to write on (round 2 research:
    Chris Donnelly's decks show each step blank, then filled, so people save them to use). It takes the recap's
    place: a recap repeats the deck; a worksheet is the slide people keep."""
    img, d = _page(t)
    _top_bar(img, d, t, num, total, name, avatar)
    mw = W - 2 * M
    prompts = [str(p).strip() for p in prompts or [] if str(p).strip()][:4]

    def content(layer, ld, y, k):
        y = _eyebrow(ld, "Your turn", M, y, t["accent"]) + int(40 * S * k)
        ft = _fit(ld, title, [z * k for z in (80, 74, 68, 62, 56)], lambda z: _display(z, t), mw, 3)
        y = _write(ld, title, ft, M, y, mw, t["ink"], int(12 * S * k)) + int(50 * S * k)
        fp = _fit(ld, max(prompts, key=len) if prompts else "", [z * k for z in (38, 35, 32, 30, 28)],
                  lambda z: _body(z, bold=True), mw, 2)
        lines = 2 if len(prompts) <= 3 else 1
        fx = _fit(ld, "e.g. " + example, [z * k for z in (34, 32, 30, 28)], lambda z: _body(z, semi=True), mw, 2) if example else None
        for i, p in enumerate(prompts):
            y = _write(ld, f"{i + 1}. {p}", fp, M, y, mw, t["ink"], int(8 * S * k))
            if i == 0 and example:   # one row filled in from the deck, so the sheet shows what an answer looks like
                y = _write(ld, "e.g. " + example, fx, M, y + int(18 * S * k), mw, t["accent"], int(8 * S * k)) + int(10 * S * k)
                ld.line([(M, y), (W - M, y)], fill=C._mix(t["bg"], t["ink"], 0.25), width=max(2, int(3 * S * k)))
                y += int(44 * S * k)
                continue
            for _ in range(lines):
                y += int(58 * S * k)
                ld.line([(M, y), (W - M, y)], fill=C._mix(t["bg"], t["ink"], 0.25), width=max(2, int(3 * S * k)))
            y += int(44 * S * k)
        fs = _body(26 * k, semi=True)
        ld.text((M, y), "Save this. Fill it in for your own work.", font=fs, fill=t["muted"])
        return y + _lh(ld, fs)

    return _done(_centred(img, content))


def _singular(unit: str) -> str:
    """'fixes' → 'fix', 'stories' → 'story', 'habits' → 'habit': to find the unit in each slide's label."""
    if unit.endswith("ies"):
        return unit[:-3] + "y"
    if unit.endswith(("xes", "ches", "shes", "sses", "zes")):
        return unit[:-2]
    return unit[:-1] if unit.endswith("s") else unit


_GENERIC_LABEL = re.compile(r"^(lesson|step|point|tip|idea|part|insight|item|no\.?|#)\s*\d{1,2}$", re.I)


_VISUAL_KINDS = ("stat", "chart", "flow", "table")
CHEATSHEET = os.getenv("DECK_CHEATSHEET", "1") != "0"


def _is_visual(s: dict) -> bool:
    """A content slide that draws its point (a number, bars, a flow, a table) rather than making one: not counted
    among the cover's points."""
    k = s.get("kind")
    return (k == "stat" and bool(s.get("stat"))) or (k == "chart" and len(s.get("bars") or []) >= 2) \
        or (k == "flow" and len(s.get("steps") or []) >= 3) or (k == "table" and len(s.get("cols") or []) >= 2
                                                                  and len(s.get("rows") or []) >= 2)


def carousel_slides(content: dict, t: dict, t_cover: dict, name: str, title: str, avatar, promise: str = "") -> list:
    """The whole deck in the v2 design, from the writer's deck (carousel.py's schema)."""
    hook, ctx = content["hook_slide"], content.get("context_slide")
    c_slides, rcp, cta = content.get("content_slides", []), content.get("recap_slide"), content["cta_slide"]
    # what most do / what works, named once for the deck ("Most boards" / "Do this")
    heads = {"wrong_head": (hook.get("wrong_head") or "").strip(), "right_head": (hook.get("right_head") or "").strip()}
    t, t_cover = {**t, **heads}, {**t_cover, **heads}
    ws = content.get("worksheet_slide") or {}
    if len([p for p in ws.get("prompts") or [] if str(p).strip()]) < 2:
        ws = {}
    unit = (hook.get("unit") or "").strip()
    one = _singular(unit)
    if one:   # a generic label takes the cover's unit: "LESSON 1" sat in a deck of "4 mistakes" (21 Nov 2026)
        n, fixed = 0, []
        for s in c_slides:
            if not _is_visual(s):
                n += 1
                lab = (s.get("label") or "").strip()
                if one.lower() not in lab.lower() and _GENERIC_LABEL.match(lab):
                    s = {**s, "label": f"{one.upper()} {n}"}
            fixed.append(s)
        c_slides = fixed
    points = [s for s in c_slides if not _is_visual(s)]
    tiles = [((s.get("icon") or "").strip().lower(), (s.get("tag") or "").strip()) for s in points]
    said = re.search(r"\b([2-9]|10)\b", hook.get("headline", ""))
    if said and int(said.group(1)) != len(points):
        tiles = []
    # The deck on one page to keep, each point and its move (22 Nov 2026 carousel audit): it comes back beside the
    # worksheet, built from the point slides themselves so it never says something the deck didn't.
    cheat = [((s.get("tag") or s.get("title") or "").strip(), (s.get("right") or s.get("action") or "").strip())
             for s in points] if CHEATSHEET else []
    cheat = cheat if len([a for a, _ in cheat if a]) >= 3 else []
    if cheat or ws:
        rcp = None
    show_overview = 3 <= len(points) <= 6 and bool(tiles) and all(tg for _, tg in tiles) and not rcp and not ws and not cheat
    counted = bool(one) and all(one.lower() in (s.get("label") or "").lower() for s in points)
    if unit and not counted and promise.startswith(f"{len(points)} {unit} · "):
        promise = promise[len(f"{len(points)} {unit} · "):]
    # one dark line to break the rhythm, after the second point (only in a deck of 3+ points)
    quote = " ".join(str(content.get("pull_quote") or "").split())
    quote_at = 2 if quote and len(points) >= 3 else -1
    total = (1 + (1 if ctx else 0) + (1 if show_overview else 0) + len(c_slides) + (1 if quote_at >= 0 else 0)
             + (1 if cheat else 0) + (1 if ws else 0) + (1 if (rcp and not cheat and not ws) else 0) + 1)
    base, kicker = C._split_hook_pill(hook.get("headline", ""))
    # the cover already shows the difference the deck is about: the first point's crossed-out and ticked lines
    first = next((s for s in points if s.get("wrong") and s.get("right")), None)
    pair = (first["wrong"], first["right"]) if first else None
    slides = [cover(t_cover, base or hook.get("headline", ""), kicker, hook.get("subtext", ""), hook.get("audience", ""),
                    promise, name, title, avatar, total, [], pair=pair)]
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
                                           (s.get("icon") or "").strip().lower(), i + 1, s.get("source", ""),
                                           s.get("wrong", ""), s.get("right", "")))
                 for i, s in enumerate(points)] or [1.0])
    pts_scales = tuple(k for k in SCALES if k <= pts_k)
    # Only the slides the cover counts get a number: "4 answers" then a PROOF slide numbered 05 broke the
    # cover's promise (22 Nov 2026 audit, round 2). Without a unit every point is counted.
    in_count = lambda s: not one or one.lower() in (s.get("label") or "").lower() or not (s.get("label") or "").strip()
    step = done_points = 0
    for s in c_slides:
        kind = s.get("kind")
        if _is_visual(s):
            if kind == "stat":
                slides.append(stat(t, num, total, name, avatar, s.get("label", ""), s["stat"], s.get("title", ""),
                                   s.get("body", ""), s.get("source", "")))
            elif kind == "chart":
                bars = [(b["label"], b["value"], b["num"]) for b in s["bars"]]
                pick = int(s.get("pick") or 0)
                slides.append(chart_slide(t, num, total, name, avatar, s.get("title", ""), bars,
                                          pick if 0 <= pick < len(bars) else 0, s.get("source", "")))
            elif kind == "flow":
                slides.append(flow_slide(t, num, total, name, avatar, s.get("title", ""), s["steps"],
                                         int(s.get("mark", -1) if s.get("mark") is not None else -1), s.get("note", "")))
            else:
                slides.append(table_slide(t, num, total, name, avatar, s.get("title", ""), s["cols"], s["rows"],
                                          bool(s.get("example"))))
        else:
            counted_here = in_count(s)
            step += counted_here
            slides.append(point(t, num, total, name, avatar, s.get("label", ""), s.get("title", ""), s.get("body", ""),
                                s.get("action", ""), (s.get("icon") or "").strip().lower(), step if counted_here else 0,
                                s.get("source", ""), pts_scales, s.get("wrong", ""), s.get("right", "")))
            done_points += 1
            if done_points == quote_at:
                num += 1
                slides.append(quote_slide(t, num, total, name, avatar, quote))
        num += 1
    if cheat:
        slides.append(cheatsheet(t, num, total, name, avatar, (rcp or {}).get("title") or "The cheat sheet", cheat))
        num += 1
    if ws:
        slides.append(worksheet(t, num, total, name, avatar, ws.get("title", "") or "Your turn", ws.get("prompts", []),
                                ws.get("example", "")))
        num += 1
    elif rcp:
        slides.append(recap(t, num, total, name, avatar, rcp.get("title", ""), rcp.get("bullets", [])))
        num += 1
    cbase, ckicker = C._split_hook_pill(cta.get("headline", ""))
    first_name = (name or "").split()[0] if name and " " in name.strip() else (name or "")
    topic = " ".join(str(cta.get("follow") or "").split())
    follow = f"Follow {first_name} for {topic}" if topic and first_name else ""
    slides.append(last(t, cbase or cta.get("headline", ""), ckicker, cta.get("cta", ""), name, title, avatar, follow))
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




# ── deck slides from the 22 Nov 2026 carousel audit ──────────────────────────────────────────────────────────────────
# Every slide in Voyce's decks was text (text-only slides: -15 to -25% in van der Blom 2024). These draw the point
# instead: a number as bars, an order as a flow, who-owns-what as a table, the deck as a cheat sheet to keep, and one
# dark line to break the rhythm. Flat, the deck's fonts (no handwriting in carousels: -25% in the same report).
def cheatsheet(t: dict, num: int, total: int, name: str, avatar, title: str, rows: list) -> Image.Image:
    """The deck on one page to keep: each point and its move, numbered (recap / cheat-sheet slides are what the best
    decks end on; the audit's decks of 11-15 pages did +16% over 1-10)."""
    img, d = _page(t)
    _top_bar(img, d, t, num, total, name, avatar)
    mw = W - 2 * M
    rows = [(str(a).strip(), str(b).strip()) for a, b in rows if str(a).strip()][:6]

    def content(layer, ld, y, k):
        y = _eyebrow(ld, "Save this", M, y, t["accent"]) + int(36 * S * k)
        ft = _fit(ld, title, [z * k for z in (76, 70, 64, 58)], lambda z: _display(z, t), mw, 2)
        y = _write(ld, title, ft, M, y, mw, t["ink"], int(10 * S * k)) + int(44 * S * k)
        nw = int(74 * S * k)
        tw = mw - nw - int(40 * S * k)
        fa = _fit(ld, max((a for a, _ in rows), key=len) if rows else "", [z * k for z in (40, 37, 34, 31)],
                  lambda z: _body(z, bold=True), tw, 2)
        fb = _fit(ld, max((b for _, b in rows), key=len) if rows else "", [z * k for z in (34, 32, 30, 28)], _body, tw - 40 * S, 2)
        fn = _display(44 * k, t)
        for i, (a, b) in enumerate(rows):
            ha = _block_h(ld, a, fa, tw, int(6 * S * k))
            hb = _block_h(ld, "→ " + b, fb, tw, int(6 * S * k)) if b else 0
            h = int(30 * S * k) + ha + (int(10 * S * k) + hb if b else 0) + int(30 * S * k)
            ld.rounded_rectangle([M, y, W - M, y + h], radius=24 * S, fill=C._mix(t["bg"], t["ink"], 0.05))
            n = str(i + 1)
            nb = ld.textbbox((0, 0), n, font=fn)
            ld.text((M + int(30 * S * k) - nb[0], y + (h - (nb[3] - nb[1])) // 2 - nb[1]), n, font=fn, fill=t["accent"])
            tx = M + nw + int(20 * S * k)
            yy = _write(ld, a, fa, tx, y + int(30 * S * k), tw, t["ink"], int(6 * S * k))
            if b:
                _write(ld, "→ " + b, fb, tx, yy + int(4 * S * k), tw, t["body"], int(6 * S * k))
            y += h + int(14 * S * k)
        return y

    return _done(_centred(img, content))


def quote_slide(t: dict, num: int, total: int, name: str, avatar, line: str) -> Image.Image:
    """One line, big, on the dark side of the look: a break in the rhythm halfway through the deck."""
    t = _loud(t)
    img, d = _page(t)
    _top_bar(img, d, t, num, total, name, avatar)
    mw = W - 2 * M

    def content(layer, ld, y, k):
        ld.rectangle([M, y, M + int(110 * S * k), y + int(10 * S * k)], fill=t["accent"])
        y += int(70 * S * k)
        f = _fit(ld, line, [z * k for z in (104, 96, 88, 82, 76, 70)], lambda z: _display(z, t), mw, 5, even=True)
        return _write(ld, line, f, M, y, mw, t["ink"], int(12 * S * k))

    return _done(_centred(img, content))


def chart_slide(t: dict, num: int, total: int, name: str, avatar, title: str, bars: list, pick: int,
                source: str = "") -> Image.Image:
    """Numbers the deck states, as horizontal bars (readable on a phone): the one the point is about in the accent,
    its value at the end of each bar, the source under them. bars: [(label, value, number)]."""
    img, d = _page(t)
    _top_bar(img, d, t, num, total, name, avatar)
    mw = W - 2 * M
    bars = [b for b in bars if b[0] and b[1]][:5]
    top = max([b[2] for b in bars] or [1]) or 1

    def content(layer, ld, y, k):
        y = _eyebrow(ld, "The numbers", M, y, t["accent"]) + int(36 * S * k)
        ft = _fit(ld, title, [z * k for z in (74, 68, 62, 58)], lambda z: _display(z, t), mw, 3)
        y = _write(ld, title, ft, M, y, mw, t["ink"], int(10 * S * k)) + int(50 * S * k)
        fl = _fit(ld, max((b[0] for b in bars), key=len) if bars else "", [z * k for z in (34, 32, 30)],
                  lambda z: _body(z, semi=True), mw, 1)
        fv = _display(52 * k, t)
        vw = max((C._tw(ld, b[1], fv) for b in bars), default=0) + int(24 * S * k)
        bh = int(64 * S * k)
        for i, (lab, val, n) in enumerate(bars):
            hot = i == pick
            ld.text((M, y), lab, font=fl, fill=t["ink"] if hot else t["body"])
            y += _lh(ld, fl) + int(10 * S * k)
            full = mw - vw
            w = max(int(full * (n / top)), int(18 * S * k))
            ld.rounded_rectangle([M, y, M + w, y + bh], radius=int(14 * S * k),
                                 fill=t["accent"] if hot else C._mix(t["bg"], t["ink"], 0.18))
            vb = ld.textbbox((0, 0), val, font=fv)
            ld.text((M + w + int(20 * S * k), y + (bh - (vb[3] - vb[1])) // 2 - vb[1]), val, font=fv,
                    fill=t["accent"] if hot else t["ink"])
            y += bh + int(30 * S * k)
        if source:
            fs = _body(26 * k)
            ld.text((M, y), C._fit_one_line(ld, f"Source: {source}", fs, mw), font=fs, fill=t["muted"])
            y += _lh(ld, fs)
        return y

    return _done(_centred(img, content))


def flow_slide(t: dict, num: int, total: int, name: str, avatar, title: str, steps: list, mark: int = -1,
               note: str = "") -> Image.Image:
    """An order the deck argues for, as boxes joined by arrows; one step marked with a note ("Most budgets start
    here"), the turn of the argument."""
    img, d = _page(t)
    _top_bar(img, d, t, num, total, name, avatar)
    mw = W - 2 * M
    steps = [str(s).strip() for s in steps if str(s).strip()][:5]

    def content(layer, ld, y, k):
        y = _eyebrow(ld, "The order", M, y, t["accent"]) + int(36 * S * k)
        ft = _fit(ld, title, [z * k for z in (74, 68, 62, 58)], lambda z: _display(z, t), mw, 3)
        y = _write(ld, title, ft, M, y, mw, t["ink"], int(10 * S * k)) + int(46 * S * k)
        fn = _body(22 * k, semi=True)
        # the note sits inside the marked box, at its right end: beside every box it squeezed them all to half width
        nw = _eyebrow_w(ld, note, 22 * k) + int(40 * S * k) if note and 0 <= mark < len(steps) else 0
        bw = mw
        fs = _fit(ld, max(steps, key=len) if steps else "", [z * k for z in (46, 43, 40, 37)], lambda z: _body(z, bold=True),
                  bw - int(150 * S * k) - (nw + int(20 * S * k) if nw else 0), 2)
        fnum = _display(46 * k, t)
        for i, s in enumerate(steps):
            hot = i == mark
            sw = bw - int(150 * S * k) - (nw + int(20 * S * k) if hot and nw else 0)
            hs = _block_h(ld, s, fs, sw, int(6 * S * k))
            h = hs + int(56 * S * k)
            ld.rounded_rectangle([M, y, M + bw, y + h], radius=24 * S,
                                 fill=C._mix(t["bg"], t["accent"], 0.18) if hot else C._mix(t["bg"], t["ink"], 0.05),
                                 outline=t["accent"] if hot else None, width=max(2, int(4 * S * k)))
            n = str(i + 1)
            nb = ld.textbbox((0, 0), n, font=fnum)
            ld.text((M + int(40 * S * k) - nb[0], y + (h - (nb[3] - nb[1])) // 2 - nb[1]), n, font=fnum, fill=t["accent"])
            _write(ld, s, fs, M + int(120 * S * k), y + (h - hs) // 2, sw, t["ink"], int(6 * S * k))
            if hot and nw:
                tx = M + bw - nw - int(24 * S * k)
                th = _lh(ld, fn) + int(24 * S * k)
                ty = y + (h - th) // 2
                ld.rounded_rectangle([tx, ty, tx + nw, ty + th], radius=th // 2, fill=t["accent"])
                _eyebrow(ld, note, tx + int(20 * S * k), ty + int(12 * S * k), _on(t["accent"], t), 22 * k)
            y += h
            if i < len(steps) - 1:
                ax = M + int(40 * S * k) + int(10 * S * k)
                ah = int(40 * S * k)
                ld.line([(ax, y + int(6 * S * k)), (ax, y + ah - int(8 * S * k))], fill=C._mix(t["bg"], t["ink"], 0.35),
                        width=max(2, int(5 * S * k)))
                ld.polygon([(ax - int(12 * S * k), y + ah - int(18 * S * k)), (ax + int(12 * S * k), y + ah - int(18 * S * k)),
                            (ax, y + ah - int(2 * S * k))], fill=C._mix(t["bg"], t["ink"], 0.35))
                y += ah
        return y

    return _done(_centred(img, content))


def table_slide(t: dict, num: int, total: int, name: str, avatar, title: str, cols: list, rows: list,
                example: bool = False) -> Image.Image:
    """Who owns what, or options against criteria: a header row in the accent, then the rows. With example, the
    first row is a filled-in example from the deck, marked as one."""
    img, d = _page(t)
    _top_bar(img, d, t, num, total, name, avatar)
    mw = W - 2 * M
    cols = [str(c).strip() for c in cols if str(c).strip()][:3]
    rows = [[str(x).strip() for x in r][:len(cols)] for r in rows if any(str(x).strip() for x in r)][:5]

    def content(layer, ld, y, k):
        y = _eyebrow(ld, "Fill this in" if example else "At a glance", M, y, t["accent"]) + int(36 * S * k)
        ft = _fit(ld, title, [z * k for z in (74, 68, 62, 58)], lambda z: _display(z, t), mw, 3)
        y = _write(ld, title, ft, M, y, mw, t["ink"], int(10 * S * k)) + int(44 * S * k)
        n = max(1, len(cols))
        cw = mw // n
        pad = int(20 * S * k)
        fh = _fit(ld, max(cols, key=len) if cols else "", [z * k for z in (30, 28, 26)], lambda z: _body(z, bold=True), cw - 2 * pad, 2)
        cells = [c for r in rows for c in r] or [""]
        fc = _fit(ld, max(cells, key=len), [z * k for z in (34, 32, 30, 28)], _body, cw - 2 * pad, 3)
        hh = max(_block_h(ld, c, fh, cw - 2 * pad, int(4 * S * k)) for c in cols) + 2 * pad if cols else 0
        ld.rounded_rectangle([M, y, W - M, y + hh], radius=20 * S, fill=t["accent"])
        for j, c in enumerate(cols):
            _write(ld, c, fh, M + j * cw + pad, y + pad, cw - 2 * pad, _on(t["accent"], t), int(4 * S * k))
        y += hh
        for i, r in enumerate(rows):
            rh = max(_block_h(ld, c or "—", fc, cw - 2 * pad, int(4 * S * k)) for c in (r + [""] * (n - len(r)))) + 2 * pad
            ex = example and i == 0
            if ex or i % 2 == 1:
                ld.rectangle([M, y, W - M, y + rh], fill=C._mix(t["bg"], t["accent"], 0.14) if ex else C._mix(t["bg"], t["ink"], 0.04))
            for j in range(n):
                c = r[j] if j < len(r) else ""
                _write(ld, c or "", fc, M + j * cw + pad, y + pad, cw - 2 * pad, t["ink"] if ex or c else t["muted"], int(4 * S * k))
            ld.line([(M, y + rh), (W - M, y + rh)], fill=t["rule"], width=max(2, int(2 * S * k)))
            y += rh
        if example:
            fs = _body(24 * k, semi=True)
            y += int(16 * S * k)
            ld.text((M, y), "Row 1 is an example. Fill in the rest for your own work.", font=fs, fill=t["muted"])
            y += _lh(ld, fs)
        return y

    return _done(_centred(img, content))



# ── single image cards ───────────────────────────────────────────────────────────────────────────
def statement_card(t: dict, line: str, emph: str, name: str, title: str, avatar) -> Image.Image:
    """A post's hook line as its picture (what hot takes, predictions and stat reactions ship with): the
    author at the top as on the post itself, the line as large as fits, its turn in the accent colour.
    4:5 in the author's look. It replaced a black 2.6:1 "tweet" strip that was the same for every client
    and a third of this height on a phone (21 Nov 2026 audit)."""
    img, d = _page(t)
    mw = W - 2 * M
    ph = 104 * S
    _author(img, d, t, avatar, name, _short_title(title), M, M, ph, 34, 25, mw - ph - 30 * S)
    top, foot = M + ph + 120 * S, H - M - 90 * S
    room = foot - top
    gap = 14 * S
    f = None
    for z in (124, 116, 108, 100, 92, 86, 80, 74, 68, 62, 56):
        cand = _display(z, t)
        lines = _wrap(d, line, cand, mw, emph)
        if len(lines) <= 7 and all(_line_w(d, ln, cand) <= mw for ln in lines) and not _ragged(lines) \
                and _block_h(d, line, cand, mw, gap, emph) <= room:
            f = cand
            break
    f = f or _display(56, t)
    y = top + max(0, (room - _block_h(d, line, f, mw, gap, emph)) // 2 - 40 * S)
    d.rectangle([M, y - 52 * S, M + 96 * S, y - 42 * S], fill=t["accent"])   # one short rule as the anchor
    _write(d, line, f, M, y, mw, t["ink"], gap, emph, t["accent"])
    _logo(img, t, W - M, H - M - 44 * S, 44 * S)
    return _done(img)


def _post_font(px: float):
    """Inter Regular for the post card's words: carousel._font() with no weight hands back Inter Bold first, and the
    best tweet cards set their text in a regular weight."""
    try:
        from PIL import ImageFont
        return ImageFont.truetype(os.path.join(C._FONTS_DIR, "Inter-Regular.ttf"), int(px * S))
    except Exception:
        return _body(px)


POST_CARD_MODES = {
    "dark":  {"bg": (14, 14, 16), "ink": (242, 242, 240), "name": (255, 255, 255), "muted": (138, 138, 142)},
    "light": {"bg": (251, 250, 247), "ink": (22, 22, 22), "name": (12, 12, 12), "muted": (110, 110, 110)},
}


def post_card(mode: str, text: str, name: str, avatar) -> Image.Image:
    """The tweet card as the best ones look (22 Nov 2026 audit, round 2: Lara Acosta, Sahil Bloom, Dan Koe, the
    220k-like Xbox card): a real post, not a poster. Flat near-black or off-white, the author's photo and name in
    bold, then the words in a regular weight at about 4-5% of the height, left aligned, line breaks kept so a
    numbered mini-story reads as one. No logo, no accent colour, no rule, no job title, no drawn likes."""
    c = POST_CARD_MODES.get(mode, POST_CARD_MODES["dark"])
    _regular = lambda px: _post_font(px)
    img = Image.new("RGB", (W, H), c["bg"])
    d = ImageDraw.Draw(img)
    pad = int(W * 0.09)
    mw = W - 2 * pad
    av = int(W * 0.12)
    fn = _body(40, bold=True)
    paras = [p.strip() for p in re.split(r"\n", (text or "").strip())]
    words = len((text or "").split())
    # the size that fills the card without a wall: big for a one-liner, ~4.5% of the height for 30-45 words
    cap = 78 if words <= 12 else 66 if words <= 20 else 60 if words <= 35 else 54
    sizes = [z for z in (78, 74, 70, 66, 62, 58, 54, 50, 47, 44, 41, 38) if z <= cap]
    head_h = av + 56 * S
    room = H - 2 * int(H * 0.10) - head_h

    def block(f):
        h, lines = 0, []
        for p in paras:
            if not p:
                lines.append(("", True))
                h += int(_lh(d, f) * 0.6)
                continue
            for ln in _wrap(d, p, f, mw):
                lines.append((" ".join(t for t, _ in ln), False))
                h += int(_lh(d, f) * 1.32)
        return h, lines
    f = _regular(sizes[-1])
    for z in sizes:
        cand = _regular(z)
        if block(cand)[0] <= room:
            f = cand
            break
    bh, lines = block(f)
    y = max(int(H * 0.10), (H - (head_h + bh)) // 2 - int(H * 0.02))
    # the author, as the post shows them
    if avatar is not None:
        a = avatar.convert("RGB").resize((av, av), Image.LANCZOS)
        m = Image.new("L", (av, av), 0)
        ImageDraw.Draw(m).ellipse([0, 0, av, av], fill=255)
        img.paste(a, (pad, y), m)
    else:
        d.ellipse([pad, y, pad + av, y + av], fill=C._mix(c["bg"], c["ink"], 0.14))
        fi = _body(av / S * 0.44, bold=True)
        ini = ((name or "V").strip()[:1] or "V").upper()
        b = d.textbbox((0, 0), ini, font=fi)
        d.text((pad + (av - (b[2] - b[0])) // 2 - b[0], y + (av - (b[3] - b[1])) // 2 - b[1]), ini, font=fi, fill=c["ink"])
    nx = pad + av + 30 * S
    d.text((nx, y + (av - _lh(d, fn)) // 2), C._fit_one_line(d, name or "", fn, W - pad - nx), font=fn, fill=c["name"])
    y += head_h
    for ln, blank in lines:
        if blank:
            y += int(_lh(d, f) * 0.6)
            continue
        d.text((pad, y), ln, font=f, fill=c["ink"])
        y += int(_lh(d, f) * 1.32)
    return _done(img)


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



# ── diagram cards (Nov 2026) ─────────────────────────────────────────────────────────────────────
# Frameworks drawn in code, not by the image model, so every word is exactly right: a 2x2 matrix,
# numbered steps, a two-column comparison and one big number. MagicPost, Postiv and Napkin sell these;
# single images out-reach documents for accounts under 5k followers (AuthoredUp, 2025-26).
def _soft(t: dict):
    """A quiet fill for a box: a tint of the ink on a light page, a lifted shade on a dark one."""
    return C._mix(t["bg"], t["ink"], 0.07) if _light(t) else C._mix(t["bg"], (255, 255, 255), 0.08)


def _accent_tint(t: dict):
    return C._mix(t["bg"], t["accent"], 0.16 if _light(t) else 0.24)


def _diagram_top(t: dict, title: str, tag: str = ""):
    """The page, its eyebrow and title; returns (img, draw, y under the title, y where the signature starts)."""
    img, d = _page(t)
    mw = W - 2 * M
    y = (_eyebrow(d, tag, M, M, t["accent"]) + 26 * S) if tag else M
    if title:
        f = _fit(d, title, (82, 76, 70, 64, 58, 52), lambda z: _display(z, t), mw, 3, even=True)
        y = _write(d, title, f, M, y, mw, t["ink"], 10 * S) + 54 * S
    return img, d, y, H - M - 130 * S


def _diagram_sign(img, d, t: dict, name: str, title: str, avatar):
    d.rectangle([M, H - M - 112 * S, W - M, H - M - 110 * S], fill=t["rule"])
    lw = _logo(img, t, W - M, H - M - 56 * S, 38 * S)
    _author(img, d, t, avatar, name, _short_title(title), M, H - M - 80 * S, 72 * S, 27, 21, W - 2 * M - 100 * S - lw)
    return _done(img)


def _centre(d, text: str, f, x0: int, y0: int, w: int, h: int, ink, gap: int):
    """Wrapped text centred in a box."""
    lines = _wrap(d, text, f, w)
    total = len(lines) * _step(d, f) + gap * max(0, len(lines) - 1)
    y = y0 + (h - total) // 2
    for line in lines:
        s = " ".join(tok for tok, _ in line)
        d.text((x0 + (w - C._tw(d, s, f)) // 2, y), s, font=f, fill=ink)
        y += _step(d, f) + gap


def _arrow(d, a: tuple, b: tuple, col, w: int, head: int):
    import math
    d.line([a, b], fill=col, width=w)
    ang = math.atan2(b[1] - a[1], b[0] - a[0])
    for s in (-1, 1):
        d.line([b, (b[0] - head * math.cos(ang + s * 0.5), b[1] - head * math.sin(ang + s * 0.5))], fill=col, width=w)


def matrix_card(t: dict, title: str, x_label: str, y_label: str, cells: list, pick: int, name: str,
                author_title: str, avatar, tag: str = "") -> Image.Image:
    """A 2x2: cells in reading order (top left, top right, bottom left, bottom right), the one that wins in the
    accent. The axes run low to high, left to right and bottom to top."""
    img, d, y, foot = _diagram_top(t, title, tag)
    cells = ([str(c).strip() for c in cells or []] + ["", "", "", ""])[:4]
    fl = _body(26, semi=True)
    axis = 70 * S
    size = min(W - M - (M + axis), foot - 40 * S - axis - y)
    gx, gy = M + axis + (W - M - (M + axis) - size) // 2, y
    gap = 18 * S
    cw = (size - gap) // 2
    fc = _fit(d, max(cells, key=len), (40, 37, 34, 31, 28), lambda z: _body(z, bold=True), cw - 56 * S, 4)
    for i, cell in enumerate(cells):
        cx, cy = gx + (i % 2) * (cw + gap), gy + (i // 2) * (cw + gap)
        fill = t["accent"] if i == pick else _soft(t)
        d.rounded_rectangle([cx, cy, cx + cw, cy + cw], radius=28 * S, fill=fill)
        _centre(d, cell, fc, cx + 28 * S, cy, cw - 56 * S, cw, _on(fill, t) if i == pick else t["ink"], 8 * S)
    col, lw, head = t["muted"], max(3, 3 * S), 18 * S
    ax = gx - 30 * S
    _arrow(d, (ax, gy + size), (ax, gy), col, lw, head)
    _arrow(d, (gx, gy + size + 30 * S), (gx + size, gy + size + 30 * S), col, lw, head)
    if x_label:
        s = C._fit_one_line(d, x_label, fl, size)
        d.text((gx + (size - C._tw(d, s, fl)) // 2, gy + size + 46 * S), s, font=fl, fill=t["body"])
    if y_label:
        s = C._fit_one_line(d, y_label, fl, size)
        tw, th = C._tw(d, s, fl), _lh(d, fl)
        lab = Image.new("RGBA", (tw + 8, th + 8), (0, 0, 0, 0))
        ImageDraw.Draw(lab).text((4, 0), s, font=fl, fill=tuple(t["body"][:3]) + (255,))
        lab = lab.rotate(90, expand=True)
        img.paste(lab, (ax - 22 * S - lab.width, gy + (size - lab.height) // 2), lab)
    return _diagram_sign(img, d, t, name, author_title, avatar)


def steps_card(t: dict, title: str, items: list, name: str, author_title: str, avatar, tag: str = "") -> Image.Image:
    """3-6 steps down a line, each a numbered circle and a short label; the last in the accent."""
    img, d, y, foot = _diagram_top(t, title, tag)
    items = [str(x).strip() for x in items or [] if str(x).strip()][:6]
    n = max(1, len(items))
    r = 40 * S
    tx = M + 2 * r + 40 * S
    row = (foot - 30 * S - y) // n
    fi = _fit(d, max(items, key=len) if items else "", (46, 43, 40, 37, 34, 31), lambda z: _body(z, bold=True), W - M - tx, 2)
    fn = _display(38, t)
    centres = [y + row * i + row // 2 for i in range(n)]
    if n > 1:
        d.line([(M + r, centres[0]), (M + r, centres[-1])], fill=t["rule"], width=max(4, 4 * S))
    for i, (it, cy) in enumerate(zip(items, centres)):
        last = i == n - 1
        fill = t["accent"] if last else (t["ink"] if _light(t) else _soft(t))
        d.ellipse([M, cy - r, M + 2 * r, cy + r], fill=fill)
        num = str(i + 1)
        b = d.textbbox((0, 0), num, font=fn)
        d.text((M + r - (b[2] - b[0]) // 2 - b[0], cy - (b[3] - b[1]) // 2 - b[1]), num, font=fn, fill=_on(fill, t))
        hb = _block_h(d, it, fi, W - M - tx, 6 * S)
        _write(d, it, fi, tx, cy - hb // 2, W - M - tx, t["accent"] if last else t["ink"], 6 * S)
    return _diagram_sign(img, d, t, name, author_title, avatar)


def compare_card(t: dict, title: str, left_head: str, right_head: str, left: list, right: list, name: str,
                 author_title: str, avatar, tag: str = "") -> Image.Image:
    """Two columns: what most do (a quiet panel, crosses) against what works (the accent's tint, ticks)."""
    img, d, y, foot = _diagram_top(t, title, tag)
    left = [str(x).strip() for x in left or [] if str(x).strip()][:4]
    right = [str(x).strip() for x in right or [] if str(x).strip()][:4]
    gap = 22 * S
    cw = (W - 2 * M - gap) // 2
    pad = 30 * S
    fh = _fit(d, max((left_head or "", right_head or ""), key=len).upper(), (30, 28, 26, 24), lambda z: _body(z, bold=True),
              cw - 2 * pad, 1)
    tw = cw - 2 * pad - 54 * S
    row_gap = 44 * S
    # the largest size at which both columns fit the space, so the panels are full rather than half empty
    fi = None
    for z in (44, 41, 38, 35, 32, 30, 28, 26):
        cand = _body(z, semi=True)
        need = max(sum(_block_h(d, r, cand, tw, 6 * S) for r in rows) + row_gap * max(0, len(rows) - 1)
                   for rows in (left, right) if rows) if (left or right) else 0
        if all(len(_wrap(d, r, cand, tw)) <= 3 for r in left + right) and \
                need <= foot - 30 * S - y - 2 * pad - _lh(d, fh) - 36 * S:
            fi = cand
            break
    fi = fi or _body(26, semi=True)
    # paired rows line up across the columns: each pair takes the taller of its two
    n_rows = max(len(left), len(right))
    heights = [max(_block_h(d, rows[i], fi, tw, 6 * S) if i < len(rows) else 0 for rows in (left, right))
               for i in range(n_rows)]
    col_h = sum(heights) + row_gap * max(0, n_rows - 1)
    bottom = min(foot - 30 * S, y + 2 * pad + _lh(d, fh) + 36 * S + col_h + 20 * S)
    shift = (foot - 30 * S - bottom) // 2   # the panels sit in the middle of the space left
    y, bottom = y + shift, bottom + shift
    for k, (head, rows, fill) in enumerate(((left_head, left, _soft(t)), (right_head, right, _accent_tint(t)))):
        x0 = M + k * (cw + gap)
        d.rounded_rectangle([x0, y, x0 + cw, bottom], radius=30 * S, fill=fill)
        hy = y + pad
        d.text((x0 + pad, hy), C._fit_one_line(d, (head or "").upper(), fh, cw - 2 * pad), font=fh,
               fill=t["accent"] if k else t["muted"])
        ry = hy + _lh(d, fh) + 36 * S
        mr = 17 * S
        for ri, row in enumerate(rows):
            cy = ry + _lh(d, fi) // 2
            if k:
                d.ellipse([x0 + pad, cy - mr, x0 + pad + 2 * mr, cy + mr], fill=t["accent"])
                d.line([(x0 + pad + mr * .55, cy + mr * .02), (x0 + pad + mr * .9, cy + mr * .38),
                        (x0 + pad + mr * 1.48, cy - mr * .32)], fill=_on(t["accent"], t), width=max(3, mr // 5), joint="curve")
            else:
                c, w = t["muted"], max(3, mr // 5)
                d.line([(x0 + pad + mr * .5, cy - mr * .5), (x0 + pad + mr * 1.5, cy + mr * .5)], fill=c, width=w)
                d.line([(x0 + pad + mr * .5, cy + mr * .5), (x0 + pad + mr * 1.5, cy - mr * .5)], fill=c, width=w)
            _write(d, row, fi, x0 + pad + 54 * S, ry, tw, t["ink"] if k else t["body"], 6 * S)
            ry += heights[ri] + row_gap
    return _diagram_sign(img, d, t, name, author_title, avatar)


def stat_card(t: dict, number: str, line: str, source: str, name: str, author_title: str, avatar, tag: str = "") -> Image.Image:
    """One number as the picture: as large as fits, what it means under it, where it comes from."""
    img, d, y, foot = _diagram_top(t, "", tag)
    mw = W - 2 * M
    fnum = _fit(d, number, (340, 300, 260, 230, 200, 170, 140), lambda z: _display(z, t), mw, 1)
    fl = _fit(d, line, (70, 64, 58, 52, 46, 42), lambda z: _display(z, t), mw, 4, even=True)
    fs = _body(26, semi=True)
    src = f"Source: {source}" if source else ""
    hn, hl = _lh(d, fnum), _block_h(d, line, fl, mw, 10 * S)
    block = hn + 40 * S + hl + ((50 * S + _lh(d, fs)) if src else 0)
    yy = y + max(0, (foot - y - block) // 2)
    d.text((M, yy), number, font=fnum, fill=t["accent"])
    yy = _write(d, line, fl, M, yy + hn + 40 * S, mw, t["ink"], 10 * S)
    if src:
        d.text((M, yy + 40 * S), C._fit_one_line(d, src, fs, mw), font=fs, fill=t["muted"])
    return _diagram_sign(img, d, t, name, author_title, avatar)


def _cover_top(im, w: int, h: int, bias: float = 0.25):
    """Scale to cover w x h, centred across and a quarter of the way down: faces sit in a photo's upper part."""
    im = im.convert("RGB")
    sw, sh = im.size
    s = max(w / sw, h / sh)
    r = im.resize((max(1, round(sw * s)), max(1, round(sh * s))), Image.LANCZOS)
    left, top = (r.width - w) // 2, int((r.height - h) * bias)
    return r.crop((left, top, left + w, top + h))


def photo_float_card(t: dict, photo, line: str, emph: str, name: str, title: str) -> Image.Image:
    """The author's own photo filling the frame, the post's line on a card floating over its lower part (Postiv's
    "Personal Pic With Quote"; a real face is the people-first picture). Flat, no gradient over the photo."""
    img = _cover_top(photo, W, H)
    d = ImageDraw.Draw(img)
    cw = W - 2 * M
    pad = 56 * S
    tw = cw - 2 * pad
    f = _fit(d, line, (84, 78, 72, 66, 60, 56), lambda z: _display(z, t), tw, 4, emph, even=True)
    hb = _block_h(d, line, f, tw, 10 * S, emph)
    fn, ft = _body(28, bold=True), _body(23)
    who = _short_title(title)
    sig_h = _lh(d, fn) + ((8 * S + _lh(d, ft)) if who else 0)
    ch = pad + hb + 36 * S + sig_h + pad
    y0 = H - M - ch
    d.rounded_rectangle([M, y0, M + cw, H - M], radius=40 * S, fill=t["bg"])
    y = _write(d, line, f, M + pad, y0 + pad, tw, t["ink"], 10 * S, emph, t["accent"]) + 36 * S - 10 * S
    lw = _logo(img, t, M + cw - pad, H - M - pad - 40 * S, 40 * S)
    d.text((M + pad, y), C._fit_one_line(d, name, fn, tw - lw - 20 * S), font=fn, fill=t["ink"])
    if who:
        d.text((M + pad, y + _lh(d, fn) + 8 * S), C._fit_one_line(d, who, ft, tw - lw - 20 * S), font=ft, fill=t["muted"])
    return _done(img)



# ── claim charts and argument shapes (22 Nov 2026 deep audit) ─────────────────────────────────────
# The best single-image posts in the audit won on what was inside: a claim as the headline, the author's own numbers
# with a source line, one accent on one element (Kyle Poyar's charts), or a shape that IS the argument: a gap, a
# spectrum, an overlap (Visualize Value, Liz Fosslien). Drawn by Voyce, so every word and number is exact.
def _grey(t: dict, k: float = 0.22):
    return C._mix(t["bg"], t["ink"], k)


def _claim_title(d, t: dict, title: str, y: int) -> int:
    mw = W - 2 * M
    f = _fit(d, title, (76, 70, 64, 58, 52), lambda z: _display(z, t), mw, 4, even=True)
    return _write(d, title, f, M, y, mw, t["ink"], 10 * S) + 50 * S


def _source_line(d, t: dict, source: str, foot: int) -> int:
    if not source:
        return foot
    fs = _body(24, semi=True)
    y = foot - _lh(d, fs) - 10 * S
    d.text((M, y), C._fit_one_line(d, f"Source: {source}", fs, W - 2 * M), font=fs, fill=t["muted"])
    return y - 24 * S


def chart_card(t: dict, title: str, bars: list, pick: int, source: str, name: str, author_title: str, avatar,
               tag: str = "") -> Image.Image:
    """A claim with the numbers behind it: horizontal bars, the one the claim is about in the accent, the rest grey,
    each value at its bar's end. bars = [(label, shown value, number)]."""
    img, d = _page(t)
    y = (_eyebrow(d, tag, M, M, t["accent"]) + 26 * S) if tag else M
    y = _claim_title(d, t, title, y)
    foot = _source_line(d, t, source, H - M - 130 * S)
    bars = [b for b in bars if b and len(b) == 3][:6]
    n = max(1, len(bars))
    top = max(b[2] for b in bars) if bars else 1
    fl, fv = _body(30, bold=True), _display(44, t)
    row = min(240 * S, (foot - y) // n)
    bh = int(row * 0.36)
    vw = max((C._tw(d, b[1], fv) for b in bars), default=0) + 24 * S
    for i, (label, shown, val) in enumerate(bars):
        ry = y + i * row
        hot = i == pick
        d.text((M, ry), C._fit_one_line(d, label, fl, W - 2 * M), font=fl, fill=t["ink"] if hot else t["body"])
        by = ry + _lh(d, fl) + 12 * S
        full = W - 2 * M - vw
        bw = max(int(full * (val / top if top else 0)), 14 * S)
        d.rounded_rectangle([M, by, M + bw, by + bh], radius=bh // 3, fill=t["accent"] if hot else _grey(t))
        d.text((M + bw + 16 * S, by + (bh - _lh(d, fv)) // 2), shown, font=fv, fill=t["accent"] if hot else t["muted"])
    return _diagram_sign(img, d, t, name, author_title, avatar)


def spectrum_card(t: dict, title: str, left: str, right: str, stops: list, pick: int, name: str, author_title: str,
                  avatar, tag: str = "") -> Image.Image:
    """A range from one end to the other with the stops along it; the stop the post argues for in the accent."""
    img, d = _page(t)
    y = (_eyebrow(d, tag, M, M, t["accent"]) + 26 * S) if tag else M
    y = _claim_title(d, t, title, y)
    foot = H - M - 130 * S
    stops = [str(s).strip() for s in stops or [] if str(s).strip()][:5]
    n = max(2, len(stops))
    ly = y + (foot - y) // 2 - 40 * S
    x0, x1 = M + 20 * S, W - M - 20 * S
    d.rounded_rectangle([x0, ly - 6 * S, x1, ly + 6 * S], radius=6 * S, fill=_grey(t))
    fe = _body(28, bold=True)
    d.text((M, ly - 70 * S), C._fit_one_line(d, left.upper(), fe, (W - 2 * M) // 2), font=fe, fill=t["muted"])
    rt = C._fit_one_line(d, right.upper(), fe, (W - 2 * M) // 2)
    d.text((W - M - C._tw(d, rt, fe), ly - 70 * S), rt, font=fe, fill=t["muted"])
    fs = _fit(d, max(stops, key=len) if stops else "", (32, 30, 28, 26, 24), lambda z: _body(z, semi=True),
              (x1 - x0) // n - 10 * S, 3)
    for i, s in enumerate(stops):
        cx = x0 + int((x1 - x0) * (i / (len(stops) - 1) if len(stops) > 1 else 0.5))
        hot = i == pick
        r = 26 * S if hot else 16 * S
        d.ellipse([cx - r, ly - r, cx + r, ly + r], fill=t["accent"] if hot else t["ink"])
        cw = (x1 - x0) // n
        bx = min(max(cx - cw // 2, M), W - M - cw)
        _centre(d, s, fs, bx, ly + 50 * S, cw, 3 * _step(d, fs), t["accent"] if hot else t["body"], 6 * S)
    return _diagram_sign(img, d, t, name, author_title, avatar)


def venn_card(t: dict, title: str, a: str, b: str, overlap: str, name: str, author_title: str, avatar,
              tag: str = "") -> Image.Image:
    """Two circles; the overlap, filled in the accent, is the point."""
    img, d = _page(t)
    y = (_eyebrow(d, tag, M, M, t["accent"]) + 26 * S) if tag else M
    y = _claim_title(d, t, title, y)
    foot = H - M - 130 * S
    fo = _body(34, bold=True)
    lab_h = 2 * _lh(d, fo) + 50 * S
    room = foot - y - lab_h
    r = min(int(room / 2) - 10 * S, int((W - 2 * M) / 3.3))
    cy = y + r + 10 * S
    cx1, cx2 = W // 2 - int(r * 0.62), W // 2 + int(r * 0.62)
    mask1 = Image.new("L", (W, H), 0); ImageDraw.Draw(mask1).ellipse([cx1 - r, cy - r, cx1 + r, cy + r], fill=255)
    mask2 = Image.new("L", (W, H), 0); ImageDraw.Draw(mask2).ellipse([cx2 - r, cy - r, cx2 + r, cy + r], fill=255)
    from PIL import ImageChops
    img.paste(t["accent"], (0, 0, W, H), ImageChops.multiply(mask1, mask2))
    w = max(4, 5 * S)
    d.ellipse([cx1 - r, cy - r, cx1 + r, cy + r], outline=t["ink"], width=w)
    d.ellipse([cx2 - r, cy - r, cx2 + r, cy + r], outline=t["ink"], width=w)
    fl = _fit(d, max((a, b), key=len), (40, 37, 34, 31, 28), lambda z: _body(z, bold=True), int(r * 0.9), 3)
    _centre(d, a, fl, cx1 - r + int(r * 0.12), cy - r, int(r * 0.9), 2 * r, t["ink"], 6 * S)
    _centre(d, b, fl, cx2 + r - int(r * 0.12) - int(r * 0.9), cy - r, int(r * 0.9), 2 * r, t["ink"], 6 * S)
    oy = cy + r + 40 * S
    d.line([(W // 2, cy + int(r * 0.55)), (W // 2, oy - 10 * S)], fill=t["accent"], width=max(3, 3 * S))
    _centre(d, overlap, _fit(d, overlap, (44, 40, 36, 32), lambda z: _display(z, t), W - 2 * M, 2), M, oy, W - 2 * M,
            lab_h - 20 * S, t["accent"], 8 * S)
    return _diagram_sign(img, d, t, name, author_title, avatar)


def gap_card(t: dict, title: str, top_label: str, bottom_label: str, gap_label: str, x_label: str, name: str,
             author_title: str, avatar, tag: str = "") -> Image.Image:
    """Two lines leaving one start: one climbs, one stays low, and the gap between them, labelled in the accent, is
    the argument (Visualize Value's earn/burn drawing)."""
    img, d = _page(t)
    y = (_eyebrow(d, tag, M, M, t["accent"]) + 26 * S) if tag else M
    y = _claim_title(d, t, title, y)
    foot = H - M - 150 * S
    x0, x1 = M + 10 * S, W - M - 260 * S
    y0, ytop, ylow = foot - 30 * S, y + 40 * S, foot - int((foot - y) * 0.28)
    w = max(5, 6 * S)
    d.line([(x0, y0), (x1, ytop)], fill=t["ink"], width=w)
    d.line([(x0, y0), (x1, ylow)], fill=_grey(t, 0.45), width=w)
    fl = _fit(d, max((top_label, bottom_label), key=len), (32, 30, 28, 26), lambda z: _body(z, bold=True), W - M - x1 - 30 * S, 2)
    _write(d, top_label, fl, x1 + 24 * S, ytop - _lh(d, fl) // 2, W - M - x1 - 24 * S, t["ink"], 4 * S)
    _write(d, bottom_label, fl, x1 + 24 * S, ylow - _lh(d, fl) // 2, W - M - x1 - 24 * S, t["muted"], 4 * S)
    bx = x1 - 40 * S
    yt = ytop + int((ylow - ytop) * (bx - x0) / (x1 - x0) * 0) + int((y0 - ytop) * (1 - (bx - x0) / (x1 - x0)))
    yb = ylow + int((y0 - ylow) * (1 - (bx - x0) / (x1 - x0)))
    d.line([(bx, yt + 14 * S), (bx, yb - 14 * S)], fill=t["accent"], width=max(4, 4 * S))
    for yy in (yt + 14 * S, yb - 14 * S):
        d.line([(bx - 16 * S, yy), (bx + 16 * S, yy)], fill=t["accent"], width=max(4, 4 * S))
    # the gap's name sits right of the bracket, in the space between the two lines' own labels
    gl = gap_label.upper()
    gx = bx + 34 * S
    width = W - M - gx
    room = (yb - yt) - 2 * (2 * _lh(d, fl) + 20 * S)
    fg = _display(24, t)
    for z in (56, 50, 44, 40, 36, 32, 28, 24):
        cand = _display(z, t)
        lines_ = _wrap(d, gl, cand, width)
        if len(lines_) <= 3 and all(_line_w(d, ln, cand) <= width for ln in lines_) \
                and _block_h(d, gl, cand, width, 4 * S) <= max(room, 60 * S):
            fg = cand
            break
    gh = _block_h(d, gl, fg, width, 4 * S)
    _write(d, gl, fg, gx, (yt + yb) // 2 - gh // 2, width, t["accent"], 4 * S)
    if x_label:
        fx = _body(24, semi=True)
        d.text((x0, y0 + 20 * S), C._fit_one_line(d, x_label, fx, x1 - x0), font=fx, fill=t["muted"])
    return _diagram_sign(img, d, t, name, author_title, avatar)


# ── round 2 formats (22 Nov 2026): checklist, before/after, self-score, ticks table, career timeline ──────────────
# The research's top formats Voyce lacked, all drawn here so every word and number is exact: Justin Welsh's checklist
# (built to be saved), Harry Dry's before/after with the result on top, Sahil Bloom's self-assessment (readers comment
# their score), Aakash Gupta's comparison table with ticks, and a career timeline from the author's own profile facts
# (the one format only the author's own history can fill).
def _row_font(d, items, sizes, max_w, lines, room, gap, bold=True):
    """The largest size at which every item fits its width in `lines` lines and all of them fit `room`."""
    for z in sizes:
        f = _body(z, bold=bold, semi=not bold)
        if all(len(_wrap(d, it, f, max_w)) <= lines for it in items) and \
                sum(_block_h(d, it, f, max_w, 6 * S) for it in items) + gap * max(0, len(items) - 1) <= room:
            return f
    return _body(sizes[-1], bold=bold, semi=not bold)


def checklist_card(t: dict, title: str, items: list, footer: str, name: str, author_title: str, avatar,
                   tag: str = "") -> Image.Image:
    """4-7 checks, each with an empty box to tick: the card people save for later."""
    img, d, y, foot = _diagram_top(t, title, tag)
    items = [str(x).strip() for x in items or [] if str(x).strip()][:7]
    box = 58 * S
    tx = M + box + 34 * S
    fs = _body(26, semi=True)
    bottom = foot - 30 * S - ((_lh(d, fs) + 30 * S) if footer else 0)
    room = bottom - y
    gap0 = 40 * S
    fi = _row_font(d, items, (54, 50, 46, 43, 40, 37, 34, 31, 28), W - M - tx, 2, room - 40 * S, gap0)
    heights = [max(_block_h(d, it, fi, W - M - tx, 6 * S), box) for it in items]
    gap = max(gap0, min(90 * S, (room - sum(heights)) // max(1, len(items))))
    yy = y + max(0, (room - sum(heights) - gap * max(0, len(items) - 1)) // 2)
    for it, h in zip(items, heights):
        by = yy + (h - box) // 2
        d.rounded_rectangle([M, by, M + box, by + box], radius=12 * S, outline=t["accent"], width=max(4, 5 * S))
        hb = _block_h(d, it, fi, W - M - tx, 6 * S)
        _write(d, it, fi, tx, yy + (h - hb) // 2, W - M - tx, t["ink"], 6 * S)
        yy += h + gap
    if footer:
        d.text((M, bottom + 4 * S), C._fit_one_line(d, footer, fs, W - 2 * M), font=fs, fill=t["muted"])
    return _diagram_sign(img, d, t, name, author_title, avatar)


def before_after_card(t: dict, title: str, before: str, after: str, before_head: str, after_head: str,
                      before_num: str, after_num: str, num_label: str, source: str, name: str, author_title: str,
                      avatar, tag: str = "") -> Image.Image:
    """The same thing before and after: a quiet panel, an arrow, the accent panel; the result (only numbers the post
    states) at the right of each, the after one large."""
    img, d, y, foot = _diagram_top(t, title, tag)
    foot = _source_line(d, t, source, foot)
    nums = bool(before_num and after_num)
    pad = 40 * S
    numw = 250 * S if nums else 0
    tw = W - 2 * M - 2 * pad - numw
    fh = _body(26, bold=True)
    arrow_h = 90 * S
    panel_room = (foot - 20 * S - y - arrow_h) // 2
    fb = None
    for z in (46, 43, 40, 37, 34, 31, 28):
        cand = _body(z, semi=True)
        need = max(_block_h(d, s, cand, tw, 8 * S) for s in (before, after)) + _lh(d, fh) + 28 * S + 2 * pad
        if need <= panel_room and all(len(_wrap(d, s, cand, tw)) <= 5 for s in (before, after)):
            fb = cand
            break
    fb = fb or _body(28, semi=True)
    ph = min(panel_room, max(_block_h(d, s, fb, tw, 8 * S) for s in (before, after)) + _lh(d, fh) + 28 * S + 2 * pad + 20 * S)
    y += max(0, (foot - 20 * S - y - (2 * ph + arrow_h)) // 2)
    for k, (head, text, num) in enumerate(((before_head or "Before", before, before_num), (after_head or "After", after, after_num))):
        py = y + k * (ph + arrow_h)
        fill = _accent_tint(t) if k else _soft(t)
        d.rounded_rectangle([M, py, W - M, py + ph], radius=30 * S, fill=fill)
        d.text((M + pad, py + pad), C._fit_one_line(d, head.upper(), fh, tw), font=fh, fill=t["accent"] if k else t["muted"])
        _write(d, text, fb, M + pad, py + pad + _lh(d, fh) + 28 * S, tw, t["ink"] if k else t["body"], 8 * S)
        if nums:
            # the largest size the number fits its slot at: cut to fit, "45%" vanished (first render, 22 Nov 2026)
            fn = next((_display(z, t) for z in ((96, 84, 74, 64, 56, 48) if k else (64, 56, 48, 40))
                       if C._tw(d, num, _display(z, t)) <= numw - pad), _display(40, t))
            s = num
            nb = d.textbbox((0, 0), s, font=fn)
            nx = W - M - pad - (nb[2] - nb[0])
            ny = py + ph // 2 - (nb[3] - nb[1]) // 2 - nb[1] - (14 * S if num_label else 0)
            d.text((nx, ny), s, font=fn, fill=t["accent"] if k else t["muted"])
            if num_label:
                fl = _body(22, semi=True)
                ls = C._fit_one_line(d, num_label, fl, numw - pad)
                d.text((W - M - pad - C._tw(d, ls, fl), ny + nb[3] + 10 * S), ls, font=fl, fill=t["muted"])
    ax = W // 2
    _arrow(d, (ax, y + ph + 16 * S), (ax, y + ph + arrow_h - 16 * S), t["accent"], max(5, 6 * S), 22 * S)
    return _diagram_sign(img, d, t, name, author_title, avatar)


def _bands(bands: list, n_items: int) -> list:
    """'0-2: Exposed' -> ('0-2', 'Exposed'); bands without ranges get them, so the bar always covers 0..n."""
    out = []
    for b in bands or []:
        s = str(b).strip()
        m = re.match(r"^\s*(\d+\s*[-–]\s*\d+|\d+\+?)\s*[:=]\s*(.+)$", s)
        out.append((m.group(1).replace(" ", "").replace("–", "-"), m.group(2).strip()) if m else ("", s))
    out = [b for b in out if b[1]][:4]
    if out and any(not r for r, _ in out):
        k = len(out)
        edges = [round(i * (n_items + 1) / k) for i in range(k + 1)]
        out = [(f"{edges[i]}-{max(edges[i], edges[i + 1] - 1)}", lab) for i, (_, lab) in enumerate(out)]
    return out


def scorecard_card(t: dict, title: str, items: list, bands: list, name: str, author_title: str, avatar,
                   tag: str = "") -> Image.Image:
    """A self-check: yes/no statements with a box each, then the score bands, the best in the accent. Readers
    count their yeses and say their score in the comments."""
    img, d, y, foot = _diagram_top(t, title, tag)
    items = [str(x).strip() for x in items or [] if str(x).strip()][:6]
    bands = _bands(bands, len(items))
    y = _eyebrow(d, "One point for every yes", M, y - 24 * S, t["muted"], 22) + 30 * S
    band_h = (150 * S) if bands else 0
    room = foot - 30 * S - band_h - 40 * S - y
    box = 46 * S
    tw = W - 2 * M - box - 40 * S
    gap0 = 30 * S
    fi = _row_font(d, items, (40, 37, 34, 31, 28, 26), tw, 2, room, gap0, bold=False)
    heights = [max(_block_h(d, it, fi, tw, 6 * S), box) for it in items]
    gap = max(gap0, min(60 * S, (room - sum(heights)) // max(1, len(items))))
    for it, h in zip(items, heights):
        _write(d, it, fi, M, y + (h - _block_h(d, it, fi, tw, 6 * S)) // 2, tw, t["ink"], 6 * S)
        bx = W - M - box
        d.rounded_rectangle([bx, y + (h - box) // 2, bx + box, y + (h - box) // 2 + box], radius=10 * S,
                            outline=t["ink"], width=max(3, 4 * S))
        y += h + gap
    if bands:
        by = foot - 30 * S - band_h
        k = len(bands)
        bw = (W - 2 * M - 12 * S * (k - 1)) // k
        fr = _display(40, t)
        for i, (rng, lab) in enumerate(bands):
            bx = M + i * (bw + 12 * S)
            best = i == k - 1
            fill = t["accent"] if best else _soft(t)
            d.rounded_rectangle([bx, by, bx + bw, by + band_h], radius=22 * S, fill=fill)
            ink = _on(fill, t) if best else t["ink"]
            d.text((bx + 24 * S, by + 20 * S), C._fit_one_line(d, rng or "", fr, bw - 48 * S), font=fr, fill=ink)
            _write(d, lab, _fit(d, lab, (25, 23, 21), lambda z: _body(z, bold=True), bw - 48 * S, 2), bx + 24 * S,
                   by + 20 * S + _lh(d, fr) + 8 * S, bw - 48 * S, ink, 4 * S)
    return _diagram_sign(img, d, t, name, author_title, avatar)


def table_card(t: dict, title: str, cols: list, rows: list, marks: list, pick: int, name: str, author_title: str,
               avatar, tag: str = "") -> Image.Image:
    """Options across, criteria down, a tick, a cross, a half-filled circle or a short word in each cell; the option
    the post argues for sits on the accent's tint."""
    img, d, y, foot = _diagram_top(t, title, tag)
    cols = [str(c).strip() for c in cols or [] if str(c).strip()][:4]
    rows = [str(r).strip() for r in rows or [] if str(r).strip()][:6]
    nc, nr = max(1, len(cols)), max(1, len(rows))
    cells = [str(m).strip() for m in marks or []] + [""] * (nc * nr)
    label_w = int((W - 2 * M) * (0.40 if nc <= 3 else 0.34))
    cw = (W - 2 * M - label_w) // nc
    fhd = _fit(d, max(cols, key=len) if cols else "", (32, 30, 28, 26, 24), lambda z: _body(z, bold=True), cw - 20 * S, 2)
    head_h = 2 * _step(d, fhd) + 40 * S
    row_h = min(150 * S, (foot - 30 * S - y - head_h) // nr)
    flab = _fit(d, max(rows, key=len) if rows else "", (34, 32, 30, 28, 26, 24), lambda z: _body(z, semi=True),
                label_w - 30 * S, 2)
    fw = _body(26, bold=True)
    top = y
    if 0 <= pick < nc:
        px = M + label_w + pick * cw
        d.rounded_rectangle([px + 6 * S, top, px + cw - 6 * S, top + head_h + nr * row_h], radius=26 * S, fill=_accent_tint(t))
    for j, c in enumerate(cols):
        _centre(d, c, fhd, M + label_w + j * cw + 10 * S, top, cw - 20 * S, head_h, t["accent"] if j == pick else t["ink"], 4 * S)
    for i, r in enumerate(rows):
        ry = top + head_h + i * row_h
        d.line([(M, ry), (W - M, ry)], fill=t["rule"], width=max(2, 2 * S))
        hb = _block_h(d, r, flab, label_w - 30 * S, 4 * S)
        _write(d, r, flab, M, ry + (row_h - hb) // 2, label_w - 30 * S, t["ink"], 4 * S)
        for j in range(nc):
            v = cells[i * nc + j].lower()
            cx, cy = M + label_w + j * cw + cw // 2, ry + row_h // 2
            rr = 24 * S
            if v in ("yes", "y", "true") or v in ("✓", "✔"):
                fill = t["accent"] if j == pick else t["ink"]
                _mark(d, True, cx, cy, rr, fill, _on(fill, t))
            elif v in ("no", "n", "x", "false") or v == "✗":
                c = C._mix(t["bg"], t["ink"], 0.35)
                w = max(3, 4 * S)
                d.line([(cx - rr * .55, cy - rr * .55), (cx + rr * .55, cy + rr * .55)], fill=c, width=w)
                d.line([(cx - rr * .55, cy + rr * .55), (cx + rr * .55, cy - rr * .55)], fill=c, width=w)
            elif v in ("part", "partly", "partial", "some", "~"):
                d.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], outline=t["muted"], width=max(3, 4 * S))
                d.pieslice([cx - rr, cy - rr, cx + rr, cy + rr], 90, 270, fill=t["muted"])
            elif v:
                _centre(d, cells[i * nc + j], fw, M + label_w + j * cw + 10 * S, ry, cw - 20 * S, row_h,
                        t["accent"] if j == pick else t["body"], 4 * S)
    return _diagram_sign(img, d, t, name, author_title, avatar)


def timeline_card(t: dict, title: str, events: list, pick: int, name: str, author_title: str, avatar,
                  tag: str = "") -> Image.Image:
    """The author's road: dated milestones down a line, the turning point in the accent. Only dates and facts the
    post or the author's profile states (checked before it gets here)."""
    img, d, y, foot = _diagram_top(t, title, tag)
    events = [(str(e.get("when", "")).strip(), str(e.get("what", "")).strip()) for e in events or [] if isinstance(e, dict)]
    events = [e for e in events if e[0] and e[1]][:6]
    n = max(1, len(events))
    fy = _fit(d, max((w for w, _ in events), key=len) if events else "", (44, 40, 36, 32, 28), lambda z: _display(z, t),
              220 * S, 1)
    yw = max((C._tw(d, w, fy) for w, _ in events), default=0)
    lx = M + yw + 46 * S
    tx = lx + 50 * S
    room = foot - 30 * S - y
    fi = _row_font(d, [w for _, w in events], (42, 39, 36, 33, 30, 28), W - M - tx, 2, room - 30 * S * n, 30 * S, bold=False)
    row = room // n
    centres = [y + row * i + row // 2 for i in range(n)]
    if n > 1:
        d.line([(lx, centres[0]), (lx, centres[-1])], fill=t["rule"], width=max(4, 5 * S))
    for i, ((when, what), cy) in enumerate(zip(events, centres)):
        hot = i == pick
        r = (22 if hot else 14) * S
        d.ellipse([lx - r, cy - r, lx + r, cy + r], fill=t["accent"] if hot else t["ink"])
        b = d.textbbox((0, 0), when, font=fy)
        d.text((lx - 46 * S - (b[2] - b[0]) - b[0], cy - (b[3] - b[1]) // 2 - b[1]), when, font=fy,
               fill=t["accent"] if hot else t["muted"])
        hb = _block_h(d, what, fi, W - M - tx, 6 * S)
        _write(d, what, fi, tx, cy - hb // 2, W - M - tx, t["accent"] if hot else t["ink"], 6 * S)
    return _diagram_sign(img, d, t, name, author_title, avatar)


# ── hand-lettered structures (22 Nov 2026 checklist audit) ──────────────────────────────────────────────────────
# The most-saved "checklists" aren't lists: they put the items in a structure that carries the logic, and they look
# hand-made. Isenberg's handwritten pairs ("OVERTHINKING → WRITE", 44.5k saves), Lieberman's 5-level ladder from least
# to most helpful (19.6k saves; readers place themselves), against a plain numbered list that reached ~3% of its
# author's followers. Drawn here in a real handwriting face (Kalam, OFL) with slightly uneven lines, so they look
# hand-lettered while every word stays exact and no image model is involved.
import random as _random

PAPER = {"bg": (247, 243, 234), "ink": (36, 34, 32), "muted": (110, 104, 96), "rule": (210, 202, 188)}


def _hand(px: float, bold: bool = False):
    from PIL import ImageFont
    name = "Kalam-Bold.ttf" if bold else "Kalam-Regular.ttf"
    try:
        return ImageFont.truetype(os.path.join(C._FONTS_DIR, name), int(px * S))
    except Exception:
        return _body(px, bold=bold)


# The same hand-made cards on three surfaces (22 Nov 2026 variety research: an author's feed of cream paper every
# day turns a rare signal into wallpaper; image after image of one kind cost 7-9% of reactions in 33,861 LinkedIn
# posts). Identity stays (the hand, the ink, the accent, the signature); the background changes.
SURFACES = {
    "paper": {"bg": (247, 243, 234), "ink": (36, 34, 32), "muted": (110, 104, 96), "rule": (210, 202, 188), "grain": True},
    "white": {"bg": (255, 255, 255), "ink": (24, 24, 27), "muted": (113, 113, 122), "rule": (228, 228, 231), "grain": False},
    "dark":  {"bg": (21, 21, 23), "ink": (242, 240, 234), "muted": (152, 150, 144), "rule": (62, 62, 66), "grain": False},
}


def _readable_on(c: tuple, bg: tuple) -> tuple:
    """The author's colour moved toward the ink (on a light page) or toward white (on a dark one) until it reads
    at 3:1 or better."""
    toward = (24, 24, 27) if C._luminance(bg) > 0.5 else (255, 255, 255)
    for k in range(0, 11):
        x = C._mix(c, toward, k * 0.08)
        if _contrast(x, bg) >= 3.0:
            return x
    return toward


def _contrast(a: tuple, b: tuple) -> float:
    def lum(rgb):   # WCAG relative luminance
        ch = [v / 255 for v in rgb]
        ch = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    la, lb = lum(a), lum(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def _on_paper(c: tuple) -> tuple:
    """The author's colour darkened until it reads on the paper (3:1 or better): a sky blue or amber house
    colour would vanish on cream."""
    def lum(rgb):   # WCAG relative luminance
        ch = [v / 255 for v in rgb]
        ch = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]

    def ratio(a, b):
        la, lb = lum(a), lum(b)
        return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)
    for k in range(0, 11):
        x = C._mix(c, PAPER["ink"], k * 0.08)
        if ratio(x, PAPER["bg"]) >= 3.0:
            return x
    return PAPER["ink"]


def _paper(accent, surface: str = "paper") -> tuple:
    """A sheet of warm paper with a faint grain (or a plain white or dark page); the author's colour only on what
    means something."""
    base = SURFACES.get(surface) or SURFACES["paper"]
    t = {k: v for k, v in base.items() if k != "grain"}
    t["surface"] = surface if surface in SURFACES else "paper"
    t["accent"] = _on_paper(tuple(accent or (200, 70, 40))) if t["surface"] == "paper" else \
        _readable_on(tuple(accent or (200, 70, 40)), t["bg"])
    img = Image.new("RGB", (W, H), t["bg"])
    if base["grain"]:
        mask = Image.effect_noise((W // 2, H // 2), 18).filter(ImageFilter.GaussianBlur(S * 0.2))
        mask = mask.point(lambda v: 70 if v > 160 else 0).resize((W, H), Image.BILINEAR)
        img.paste(C._mix(t["bg"], (120, 100, 80), 0.07), (0, 0, W, H), mask)
    return img, ImageDraw.Draw(img), t


def _wobble(d, a: tuple, b: tuple, col, w: int, rnd, amp: float = 1.6):
    """A line as a hand draws it: a few segments, each a hair off the straight path."""
    import math
    n = max(3, int(math.hypot(b[0] - a[0], b[1] - a[1]) / (60 * S)))
    pts = []
    for i in range(n + 1):
        f = i / n
        jitter = 0 if i in (0, n) else rnd.uniform(-amp, amp) * S
        dx, dy = b[0] - a[0], b[1] - a[1]
        ln = math.hypot(dx, dy) or 1
        pts.append((a[0] + dx * f - dy / ln * jitter, a[1] + dy * f + dx / ln * jitter))
    d.line(pts, fill=col, width=w, joint="curve")


def _hand_arrow(d, a: tuple, b: tuple, col, w: int, rnd):
    _wobble(d, a, b, col, w, rnd)
    hl = 16 * S
    d.line([(b[0] - hl, b[1] - hl * 0.6), b], fill=col, width=w, joint="curve")
    d.line([(b[0] - hl, b[1] + hl * 0.6), b], fill=col, width=w, joint="curve")


def _hand_box(d, box: tuple, col, w: int, rnd):
    x0, y0, x1, y1 = box
    for a, b in (((x0, y0), (x1, y0)), ((x1, y0), (x1, y1)), ((x1, y1), (x0, y1)), ((x0, y1), (x0, y0))):
        _wobble(d, a, b, col, w, rnd, 2.2)


def _hand_sign(d, t: dict, sig: str):
    if sig:
        f = _hand(30)
        d.text((W - M - C._tw(d, sig, f), H - M - _lh(d, f)), sig, font=f, fill=t["muted"])


def _hand_title(d, t: dict, title: str, sub: str, rnd) -> int:
    """The name of the thing, hand-lettered inside a hand-drawn box; the y under it."""
    mw = W - 2 * M - 80 * S
    f = _fit(d, title.upper(), (64, 58, 52, 47, 42, 38), lambda z: _hand(z, bold=True), mw, 2)
    fs = _hand(30)
    th = _block_h(d, title.upper(), f, mw, 4 * S) + ((_lh(d, fs) + 6 * S) if sub else 0)
    tw = max([_line_w(d, ln, f) for ln in _wrap(d, title.upper(), f, mw)] + [C._tw(d, sub, fs) if sub else 0])
    x0, y0 = M, M + 10 * S
    _hand_box(d, (x0, y0, x0 + tw + 70 * S, y0 + th + 50 * S), t["ink"], max(3, 4 * S), rnd)
    y = _write(d, title.upper(), f, x0 + 35 * S, y0 + 22 * S, mw, t["ink"], 4 * S)
    if sub:
        d.text((x0 + 35 * S, y + 2 * S), sub, font=fs, fill=t["muted"])
    return y0 + th + 50 * S


def pairs_card(accent, title: str, sub: str, pairs: list, sig: str = "", surface: str = "paper") -> Image.Image:
    """Isenberg's handwritten pairs: a state the reader recognises → the one thing to do (4-8 pairs, ~60 words).
    Both columns wrap; the biggest size where every row fits wins, and the rows sit as one block, never spread thin."""
    img, d, t = _paper(accent, surface)
    rnd = _random.Random(sum(map(ord, title)) & 0xFFFF)
    pairs = [(str(a).strip(), str(b).strip()) for a, b in pairs if str(a).strip() and str(b).strip()][:8]
    top = _hand_title(d, t, title, sub, rnd) + 60 * S
    foot = H - M - 80 * S
    avail, arrow = W - 2 * M, 150 * S
    for z in (76, 70, 64, 58, 54, 50, 46, 43, 40, 37, 34, 31, 28):
        fl, fr = _hand(z, bold=True), _hand(z)
        lw = min(max([C._tw(d, a.upper(), fl) for a, _ in pairs] or [0]), int(avail * 0.48))
        rw = avail - lw - arrow
        words_fit = all(C._tw(d, w, fl) <= lw for a, _ in pairs for w in a.upper().split()) and \
            all(C._tw(d, w, fr) <= rw for _, b in pairs for w in b.upper().split())
        rows = [max(_block_h(d, a.upper(), fl, lw, 2 * S), _block_h(d, b.upper(), fr, rw, 2 * S)) for a, b in pairs]
        gap = int(z * S * 0.9)
        two = all(len(_wrap(d, a.upper(), fl, lw)) <= 2 and len(_wrap(d, b.upper(), fr, rw)) <= 2 for a, b in pairs)
        if words_fit and two and sum(rows) + gap * (len(rows) - 1) <= foot - top:
            break
    gap = min(int(z * S * 1.6), (foot - top - sum(rows)) // max(1, len(rows) - 1)) if len(rows) > 1 else 0
    y = top + max(0, (foot - top - sum(rows) - gap * (len(rows) - 1)) // 3)     # a little above centre
    for (a, b), h in zip(pairs, rows):
        ha, hb = _block_h(d, a.upper(), fl, lw, 2 * S), _block_h(d, b.upper(), fr, rw, 2 * S)
        _write(d, a.upper(), fl, M, y + (h - ha) // 2, lw, t["ink"], 2 * S)
        cy = y + h // 2
        _hand_arrow(d, (M + lw + 28 * S, cy), (M + lw + arrow - 34 * S, cy), t["accent"], max(3, 4 * S), rnd)
        _write(d, b.upper(), fr, M + lw + arrow, y + (h - hb) // 2, rw, t["ink"], 2 * S)
        y += h + gap
    _hand_sign(d, t, sig)
    return _done(img)


def ladder_card(accent, title: str, levels: list, low: str = "Least helpful", high: str = "Most helpful",
                sig: str = "", surface: str = "paper") -> Image.Image:
    """Lieberman's ladder: 3-5 levels from worst to best (quotes or habits), the steps of a hand-drawn pyramid; the
    reader finds the level they're on. levels go least to most."""
    img, d, t = _paper(accent, surface)
    rnd = _random.Random(sum(map(ord, title)) & 0xFFFF)
    levels = [str(x).strip() for x in levels if str(x).strip()][:5]
    n = max(2, len(levels))
    y0 = _hand_title(d, t, title, "", rnd) + 60 * S
    foot = H - M - 80 * S
    py_w = int((W - 2 * M) * 0.38)
    px0, px1 = W - M - py_w, W - M
    cx = (px0 + px1) // 2
    h_total = foot - y0
    tw = px0 - M - 40 * S
    fq = _fit(d, max(levels, key=len) if levels else "", (34, 31, 28, 26, 24), lambda z: _hand(z), tw, 4)
    fn = _hand(46, bold=True)
    fl = _hand(24)
    seg = h_total // n
    for i in range(n):            # i = 0 is the top (best)
        lvl = n - i
        top, bot = y0 + i * seg, y0 + (i + 1) * seg
        hw_top, hw_bot = py_w / 2 * (i / n), py_w / 2 * ((i + 1) / n)
        poly = [(cx - hw_top, top), (cx + hw_top, top), (cx + hw_bot, bot), (cx - hw_bot, bot)]
        if i == 0:
            d.polygon(poly, fill=C._mix(t["bg"], t["accent"], 0.28))
        for a, b in ((poly[1], poly[2]), (poly[3], poly[0]), (poly[2], poly[3])):
            _wobble(d, a, b, t["accent"] if i == 0 else t["ink"], max(3, 4 * S), rnd, 1.4)
        num = str(lvl)
        nb = d.textbbox((0, 0), num, font=fn)
        d.text((cx - (nb[2] - nb[0]) // 2 - nb[0], (top + bot) // 2 - (nb[3] - nb[1]) // 2 - nb[1] + (8 * S if i == 0 else 0)),
               num, font=fn, fill=t["accent"] if i == 0 else t["ink"])
        q = levels[lvl - 1] if lvl - 1 < len(levels) else ""
        hb = _block_h(d, q, fq, tw, 4 * S)
        _write(d, q, fq, M, (top + bot) // 2 - hb // 2, tw, t["ink"], 4 * S)
    d.text((cx - C._tw(d, high, fl) // 2, y0 - _lh(d, fl) - 6 * S), high, font=fl, fill=t["accent"])
    d.text((cx - C._tw(d, low, fl) // 2, foot + 10 * S), low, font=fl, fill=t["muted"])
    _hand_sign(d, t, sig)
    return _done(img)


# ── one-line opinion cards (22 Nov 2026 opinion-visual research) ─────────────────────────────────────────────────────
# Across 12 B2B creators' 650 image posts (each against the same creator's median), a handwritten note was the only
# single-line treatment that beat the creator's baseline across many creators (8 creators, 1.2-1.9x views, ~2.8x
# likes); a printed-page excerpt with the claim in bold last beat it every time (9/9, mostly Justin Welsh). Designed
# statement cards sat at ~0.95x and tweet-screenshot cards at 0.5-0.7x. Both drawn exactly, no image model.
INK_PEN = (28, 38, 72)          # ballpoint blue-black


def _serif(px: float, bold: bool = False):
    from PIL import ImageFont
    try:
        return ImageFont.truetype(os.path.join(C._FONTS_DIR, "CrimsonText-Bold.ttf" if bold else "CrimsonText-Regular.ttf"),
                                  int(px * S))
    except Exception:
        return _body(px, bold=bold)


def _hand_words(img, words: list, font, x: int, y: int, ink, rnd) -> list:
    """Each word drawn on its own, a hair rotated, raised and resized, so the line reads as written by hand rather
    than typeset. The boxes drawn, for underlining."""
    sp = C._tw(ImageDraw.Draw(img), " ", font)
    boxes = []
    for w in words:
        f = font
        z = int(font.size * rnd.uniform(0.97, 1.03))
        try:
            f = font.font_variant(size=z)
        except Exception:
            pass
        d0 = ImageDraw.Draw(img)
        bb = d0.textbbox((0, 0), w, font=f)
        pad = 12 * S
        tile = Image.new("L", (bb[2] - bb[0] + 2 * pad, bb[3] + 2 * pad), 0)
        ImageDraw.Draw(tile).text((pad - bb[0], pad), w, font=f, fill=int(255 * rnd.uniform(0.86, 1.0)))
        tile = tile.rotate(rnd.uniform(-1.4, 1.4), resample=Image.BICUBIC, expand=True)
        dy = int(rnd.uniform(-2.2, 2.2) * S)
        img.paste(Image.new("RGB", tile.size, ink), (x - pad, y - pad + dy), tile)
        boxes.append((w, x, y + dy, x + bb[2] - bb[0], y + dy + bb[3]))
        x += bb[2] - bb[0] + sp
    return boxes


def _pen(px: float):
    """Caveat, a natural pen hand (the craft judge called Kalam 'Comic Sans-like' on a one-line note); Kalam if it's
    missing. Caveat runs small, so it is set a quarter larger."""
    from PIL import ImageFont
    try:
        return ImageFont.truetype(os.path.join(C._FONTS_DIR, "Caveat-Variable.ttf"), int(px * S * 1.25))
    except Exception:
        return _hand(px)


def note_card(accent, line: str, emphasis: str = "", sig: str = "", surface: str = "paper") -> Image.Image:
    """The post's one line written in pen on plain paper, a flat scan, signed small (Sahil Bloom's and Gary Vee's
    handwritten notes): 8-30 words, one ink, big hand at ~65% of the width, one hand-drawn underline for emphasis."""
    img, d, t = _paper(accent, surface)
    pen = INK_PEN if t["surface"] != "dark" else t["ink"]   # chalk-white on the dark page
    rnd = _random.Random(sum(map(ord, line)) & 0xFFFF)
    line = " ".join(str(line or "").split())
    mx = int(W * 0.14)
    mw = W - 2 * mx
    for z in (110, 100, 92, 84, 77, 70, 64, 58, 53, 48):
        f = _pen(z)
        lines = [[w for w, _ in ln] for ln in _wrap(d, line, f, mw)]
        step = int(_lh(d, f) * 1.18)
        if len(lines) * step <= H * 0.62:
            break
    y = (H - len(lines) * step) // 2 - int(H * 0.03)
    em = [w.lower().strip(".,;:!?\"'") for w in str(emphasis or "").split()]
    for ln in lines:
        x = mx + int(rnd.uniform(-6, 10) * S)          # a hand never starts every line at the same point
        boxes = _hand_words(img, ln, f, x, y, pen, rnd)
        hot = [b for b in boxes if b[0].lower().strip(".,;:!?\"'") in em]
        if hot:
            ux0, ux1 = hot[0][1] - 6 * S, hot[-1][3] + 8 * S
            uy = max(b[4] for b in hot) + 10 * S
            _wobble(d, (ux0, uy), (ux1, uy + int(rnd.uniform(-4, 4) * S)), pen, max(3, 5 * S), rnd, 2.0)
        y += step
    if sig:
        fs = _pen(40)
        d.text((W - mx - C._tw(d, "— " + sig, fs), H - int(H * 0.12)), "— " + sig, font=fs, fill=pen)
    return _done(img)


def typed_note(accent, text: str, sig: str = "", surface: str = "dark") -> Image.Image:
    """A plain typed note: the take in a regular face, left-aligned, nothing else on the page but a small signature
    (Dan Koe's black text cards: 1.27x the creator's median views across 24 posts by 5 creators, 22 Nov 2026 variety
    research). No fake app or post chrome: that is what sank the tweet card. Paragraphs kept; 15-120 words."""
    img, d, t = _paper(accent, surface)
    paras = [" ".join(p.split()) for p in str(text or "").split("\n\n") if p.strip()][:4]
    mx = int(W * 0.12)
    mw = W - 2 * mx
    for z in (66, 60, 55, 50, 46, 42, 39, 36, 33):
        f = _post_font(z)
        gap = int(z * S * 0.34)
        pgap = int(z * S * 1.25)
        h = sum(_block_h(d, p, f, mw, gap) for p in paras) + pgap * (len(paras) - 1)
        if h <= H * 0.66:
            break
    y = (H - h) // 2 - int(H * 0.03)
    for i, p in enumerate(paras):
        y = _write(d, p, f, mx, y, mw, t["ink"], gap) - gap + (pgap if i < len(paras) - 1 else 0)
    if sig:
        fs = _post_font(28)
        d.text((mx, H - int(H * 0.11)), "— " + sig, font=fs, fill=t["muted"])
    return _done(img)


def page_card(accent, setup: str, claim: str, byline: str = "") -> Image.Image:
    """A cropped book page (Justin Welsh's printed excerpts): two or three sentences of setup in a serif, the claim
    in bold as the last line. Never a made-up book title or running head; the author's name small at the foot."""
    img, d, t = _paper(accent)
    img.paste((249, 247, 241), (0, 0, W, H))      # whiter than the note paper: a printed page
    t["bg"] = (249, 247, 241)
    mask = Image.effect_noise((W // 2, H // 2), 14).point(lambda v: 40 if v > 170 else 0).resize((W, H), Image.BILINEAR)
    img.paste((228, 222, 210), (0, 0, W, H), mask)
    d = ImageDraw.Draw(img)
    setup, claim = " ".join(str(setup or "").split()), " ".join(str(claim or "").split())
    mx = int(W * 0.13)
    mw = W - 2 * mx
    ink = (34, 32, 30)
    for z in (60, 56, 52, 49, 46, 43, 40, 37):
        fr, fb = _serif(z), _serif(z, bold=True)
        gap = int(z * S * 0.24)
        hs = _block_h(d, setup, fr, mw, gap) if setup else 0
        hc = _block_h(d, claim, fb, mw, gap)
        total = hs + (int(z * S * 1.1) if setup else 0) + hc
        if total <= H * 0.66:
            break
    y = (H - total) // 2 - int(H * 0.02)
    if setup:
        y = _write(d, setup, fr, mx, y, mw, ink, gap) + int(z * S * 1.1) - gap
    _write(d, claim, fb, mx, y, mw, ink, gap)
    if byline:
        fs = _serif(30)
        txt = byline.upper()
        tw = sum(C._tw(d, ch, fs) for ch in txt) + 4 * S * (len(txt) - 1)
        x = (W - tw) // 2
        for ch in txt:                                    # letter-spaced small caps at the foot of the page
            d.text((x, H - int(H * 0.09)), ch, font=fs, fill=(120, 114, 106))
            x += C._tw(d, ch, fs) + 4 * S
    return _done(img)
