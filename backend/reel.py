"""
reel.py — render a Voyce reel to a finished MP4, unattended.

Screen-recording a browser was the weak link: it needs a human. This draws every
frame with PIL and pipes them into ffmpeg, muxing the synced music bed in the
same pass. Input is a spec dict, output is a 1080x1920 H.264/AAC file ready for
instagram.publish_reel().

The visual system matches the designed template: navy mesh is the constant
brand ground, the paper panel rises at the narrative turn, and the payoff
returns to navy so the loop closes on the frame it opened on.

Backgrounds are rendered once and reused across frames — per-pixel gradient work
every frame would make this unusably slow. Only animated elements redraw.
"""

import math
import os
import shutil
import subprocess

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H = 1080, 1920
FPS = 30

FONT_DIR = os.path.join(os.path.dirname(__file__), "fonts")

# Brand palette — same values as frontend/landing.html
INK = (20, 22, 27)
INK_SOFT = (91, 98, 112)
PAPER = (255, 255, 255)
WASH = (231, 235, 244)
WASH_2 = (211, 220, 238)
NAVY = (36, 54, 94)
NAVY_MID = (49, 73, 126)
NAVY_DEEP = (22, 35, 63)
NIGHT = (11, 18, 32)
PERI = (143, 165, 214)
PERI_SOFT = (183, 198, 232)
DEAD = (58, 67, 86)
LIVE = (47, 163, 122)


# ── Fonts ────────────────────────────────────────────────────────────────────
_font_cache: dict = {}


def _f(kind: str, size: int, weight: int = 700):
    """kind: display (Space Grotesk) | body (Inter) | serif (Fraunces italic)."""
    key = (kind, size, weight)
    if key in _font_cache:
        return _font_cache[key]
    if kind == "display":
        f = ImageFont.truetype(os.path.join(FONT_DIR, "SpaceGrotesk.ttf"), size)
        try:
            f.set_variation_by_axes([weight])
        except Exception:
            pass
    elif kind == "serif":
        f = ImageFont.truetype(os.path.join(FONT_DIR, "Fraunces-Italic.ttf"), size)
        try:
            # opsz, wght, SOFT, WONK — big optical size keeps the V elegant
            f.set_variation_by_axes([144, weight, 0, 1])
        except Exception:
            pass
    else:
        name = {900: "Inter-Black.ttf", 700: "Inter-Bold.ttf",
                600: "Inter-SemiBold.ttf", 400: "Inter-Regular.ttf"}.get(weight, "Inter-SemiBold.ttf")
        f = ImageFont.truetype(os.path.join(FONT_DIR, name), size)
    _font_cache[key] = f
    return f


def _tw(draw, text, font):
    return draw.textbbox((0, 0), text, font=font)[2]


# ── Easing ───────────────────────────────────────────────────────────────────
def ease_out(t: float) -> float:
    """cubic-bezier(0.16, 1, 0.3, 1) — the template's default decel."""
    t = max(0.0, min(1.0, t))
    return 1 - pow(1 - t, 3.2)


def ease_dram(t: float) -> float:
    """Slow wind-up, hard release — used for the paper rise and the hook cut."""
    t = max(0.0, min(1.0, t))
    return 3 * t * t - 2 * t * t * t if t < 1 else 1.0


def spring(t: float, overshoot: float = 1.09) -> float:
    """Damped spring with a small overshoot, matching the CSS linear() curve."""
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    return 1 - math.exp(-6.5 * t) * math.cos(7.0 * t) * (1 / overshoot)


def spring_pop(t: float) -> float:
    """Bouncier — the stamp landing."""
    if t <= 0:
        return 0.0
    if t >= 1:
        return 1.0
    return 1 - math.exp(-5.0 * t) * math.cos(9.5 * t)


# ── Grounds (built once, reused every frame) ──────────────────────────────────
def _radial(shape, cx, cy, rx, ry):
    """Normalised radial falloff field, 1.0 at the centre."""
    h, w = shape
    y, x = np.ogrid[0:h, 0:w]
    d = np.sqrt(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2)
    return np.clip(1.0 - d, 0.0, 1.0)


def _lin_v(shape, top, bottom):
    h, w = shape
    ramp = np.linspace(0, 1, h)[:, None, None]
    return np.array(top)[None, None, :] * (1 - ramp) + np.array(bottom)[None, None, :] * ramp


def ground_navy(w=W, h=H) -> Image.Image:
    """The account's constant: a navy mesh with two lit corners."""
    base = _lin_v((h, w), (16, 25, 44), (7, 13, 24))
    base = base + np.zeros((h, w, 3))
    for (cx, cy, rx, ry, col, amt) in [
        (0.18 * w, 0.08 * h, 0.78 * w, 0.34 * h, (49, 73, 126), 0.62),
        (0.88 * w, 0.74 * h, 0.66 * w, 0.30 * h, (30, 48, 88), 0.72),
        (0.50 * w, 1.08 * h, 0.90 * w, 0.40 * h, (143, 165, 214), 0.14),
    ]:
        f = _radial((h, w), cx, cy, rx, ry)[:, :, None] ** 1.6
        base = base * (1 - f * amt) + np.array(col)[None, None, :] * (f * amt)
    return Image.fromarray(np.clip(base, 0, 255).astype("uint8"))


def ground_paper(w=W, h=H) -> Image.Image:
    """The turn: paper. A pure linear wash, exactly like the landing page — any
    radial highlight here renders as a grey blob rather than as light, which is
    the trap the first version fell into."""
    base = _lin_v((h, w), (247, 249, 253), (205, 216, 236)) + np.zeros((h, w, 3))
    return Image.fromarray(np.clip(base, 0, 255).astype("uint8"))


def vignette_overlay(w=W, h=H) -> Image.Image:
    """Corner darkening for the NAVY surfaces only. Squared falloff keeps the
    transition invisible — a linear ramp shows its own boundary as an oval,
    which reads as a blob rather than as shading."""
    f = 1.0 - _radial((h, w), w * 0.5, h * 0.46, w * 1.45, h * 1.05)
    a = np.clip((f ** 2) * 420, 0, 62).astype("uint8")
    rgba = np.zeros((h, w, 4), dtype="uint8")
    rgba[:, :, 3] = a
    rgba[:, :, 0:3] = np.array([4, 8, 16])[None, None, :]
    return Image.fromarray(rgba, "RGBA")


def grain_tile(size=320, seed=3) -> Image.Image:
    rng = np.random.default_rng(seed)
    n = rng.integers(0, 255, (size, size), dtype="uint8")
    rgba = np.zeros((size, size, 4), dtype="uint8")
    rgba[:, :, 0] = rgba[:, :, 1] = rgba[:, :, 2] = n
    rgba[:, :, 3] = 10
    return Image.fromarray(rgba, "RGBA")


# ── Text helpers ─────────────────────────────────────────────────────────────
def wrap_words(draw, words, font, max_w):
    """Group words into lines that fit, keeping word objects intact so each can
    animate on its own schedule."""
    lines, cur = [], []
    for wd in words:
        trial = " ".join([w["t"] for w in cur + [wd]])
        if cur and _tw(draw, trial, font) > max_w:
            lines.append(cur)
            cur = [wd]
        else:
            cur.append(wd)
    if cur:
        lines.append(cur)
    return lines


def draw_words(img, words, font, x, y, max_w, t, line_gap=1.13, color=PAPER,
               accent=PERI, stagger=0.088, rise=0.64):
    """Word-by-word masked reveal. The CSS does a 3D flip; PIL can't, so this
    degrades to translate-up plus a fade, which reads almost identically at
    30fps on a phone."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    lines = wrap_words(probe, words, font, max_w)
    asc = font.getbbox("Hg")[3]
    lh = int(asc * line_gap)
    idx = 0
    cy = y
    for line in lines:
        cx = x
        for wd in line:
            start = wd.get("at", 0.0) + idx * stagger
            p = ease_out((t - start) / rise) if t > start else 0.0
            if p > 0.002:
                off = int((1 - spring(min(1.0, (t - start) / rise))) * asc * 0.95)
                col = accent if wd.get("accent") else color
                d.text((cx, cy + off), wd["t"], font=font, fill=col + (int(255 * min(1.0, p * 1.35)),))
            cx += _tw(probe, wd["t"] + " ", font)
            idx += 1
        cy += lh
    img.alpha_composite(layer)
    return cy


def draw_lockup(d, x, y, size, on_dark=True):
    """The brand mark: an oversized Fraunces italic V flowing into 'oyce'.
    y is the shared BASELINE — anchoring both faces to it is what makes the
    pair read as one word instead of two stacked pieces."""
    vf = _f("serif", int(size * 1.9), 500)
    wf = _f("display", size, 700)
    col = PERI_SOFT if on_dark else NAVY
    d.text((x, y), "V", font=vf, fill=col, anchor="ls")
    adv = vf.getlength("V")
    d.text((x + adv - size * 0.13, y), "oyce", font=wf, fill=col, anchor="ls")


def rounded(d, box, r, fill=None, outline=None, width=1):
    d.rounded_rectangle(box, radius=r, fill=fill, outline=outline, width=width)


# ── Devices ──────────────────────────────────────────────────────────────────
# Each takes (img, t, t0) where t0 is when the device begins, and draws itself
# at whatever state that implies. Problem devices sit on navy, turn devices on
# paper — the same split the designed template uses.

def dev_clocknight(img, t, t0):
    """11:04 PM, blank composer, blinking cursor — the problem, on navy."""
    p = spring(min(1.0, (t - t0) / 0.7)) if t > t0 else 0.0
    if p <= 0.01:
        return
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(255 * min(1.0, p * 1.4))
    yoff = int((1 - p) * 60)
    x0, y0, w, h = 96, 1090 + yoff, W - 192, 216
    rounded(d, (x0, y0, x0 + w, y0 + h), 30, fill=(6, 11, 22, int(a * 0.66)),
            outline=PERI + (int(a * 0.30),), width=3)
    d.text((x0 + 46, y0 + 84), "11:04 PM", font=_f("body", 62, 700),
           fill=(147, 160, 183, a), anchor="ls")
    sf = _f("body", 40, 600)
    msg = "Blank page."
    d.text((x0 + 46, y0 + 152), msg, font=sf, fill=(119, 131, 154, a), anchor="ls")
    if t > t0 + 0.5 and int((t - t0) * 2) % 2 == 0:
        cx = x0 + 46 + int(sf.getlength(msg)) + 12
        d.rectangle((cx, y0 + 118, cx + 6, y0 + 156), fill=(119, 131, 154, a))
    img.alpha_composite(layer)


def dev_clockday(img, t, t0):
    """8:00 AM, published — the turn, on paper."""
    p = spring_pop(min(1.0, (t - t0) / 0.8)) if t > t0 else 0.0
    if p <= 0.01:
        return
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(255 * min(1.0, p * 1.5))
    yoff = int((1 - p) * 70)
    x0, y0, w, h = 96, 1110 + yoff, W - 192, 226
    rounded(d, (x0 + 6, y0 + 12, x0 + w + 6, y0 + h + 12), 30, fill=(20, 32, 60, int(a * 0.15)))
    rounded(d, (x0, y0, x0 + w, y0 + h), 30, fill=PAPER + (a,),
            outline=NAVY + (int(a * 0.14),), width=3)
    d.text((x0 + 46, y0 + 88), "8:00 AM", font=_f("body", 62, 700), fill=NAVY + (a,), anchor="ls")
    d.text((x0 + 46, y0 + 160), "Your take, in your voice.", font=_f("body", 40, 600),
           fill=INK + (a,), anchor="ls")
    bf = _f("display", 32, 700)
    bw = int(bf.getlength("live")) + 52
    bx = x0 + w - bw - 34
    rounded(d, (bx, y0 + 40, bx + bw, y0 + 96), 28, fill=LIVE + (a,))
    d.text((bx + bw // 2, y0 + 78), "live", font=bf, fill=PAPER + (a,), anchor="ms")
    img.alpha_composite(layer)


def dev_dots(img, t, t0):
    """30 posting days lighting up, then going out — the problem, on navy."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    cols, gap, r = 10, 74, 22
    x0 = (W - (cols - 1) * gap) // 2
    y0 = 1150
    for i in range(30):
        on = t0 + i * 0.026
        off = t0 + 1.25 + i * 0.036
        if t < on:
            continue
        pin = spring_pop(min(1.0, (t - on) / 0.4))
        pout = ease_out(min(1.0, (t - off) / 0.5)) if t > off else 0.0
        rr = int(r * (0.35 + 0.65 * pin) * (1 - 0.3 * pout))
        col = tuple(int(PERI[k] + (DEAD[k] - PERI[k]) * pout) for k in range(3))
        a = int(255 * (min(1.0, pin) * (1 - 0.35 * pout)))
        cx, cy = x0 + (i % cols) * gap, y0 + (i // cols) * gap
        if pout < 0.5:  # glow while alive
            d.ellipse((cx - rr - 8, cy - rr - 8, cx + rr + 8, cy + rr + 8),
                      fill=PERI + (int(a * 0.16),))
        d.ellipse((cx - rr, cy - rr, cx + rr, cy + rr), fill=col + (a,))
    img.alpha_composite(layer)


def dev_feed(img, t, t0):
    """Three posts draining to grey, then an empty slot — problem, on navy."""
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    x0, w = 116, W - 232
    for i in range(3):
        on = t0 + i * 0.13
        die = t0 + 1.3 + i * 0.30
        if t < on:
            continue
        pin = spring(min(1.0, (t - on) / 0.5))
        pd = ease_out(min(1.0, (t - die) / 0.6)) if t > die else 0.0
        y0 = 1080 + i * 132 + int((1 - pin) * 58)
        a = int(255 * min(1.0, pin) * (1 - 0.7 * pd))
        card = tuple(int(255 * 0.075 + (DEAD[k] - 255 * 0.075) * pd * 0.4) for k in range(3))
        rounded(d, (x0, y0, x0 + w, y0 + 112), 26, fill=card + (int(a * 0.9),),
                outline=PERI + (int(a * 0.22),), width=2)
        av = tuple(int(PERI[k] + (DEAD[k] - PERI[k]) * pd) for k in range(3))
        d.ellipse((x0 + 26, y0 + 30, x0 + 78, y0 + 82), fill=av + (a,))
        for j, ww in enumerate((0.62, 0.40)):
            d.rounded_rectangle((x0 + 100, y0 + 38 + j * 30, x0 + 100 + int(w * 0.55 * ww),
                                 y0 + 52 + j * 30), radius=8, fill=PERI_SOFT + (int(a * 0.5),))
        tick = tuple(int(LIVE[k] + (DEAD[k] - LIVE[k]) * pd) for k in range(3))
        d.text((x0 + w - 66, y0 + 38), "OK", font=_f("body", 34, 700), fill=tick + (a,))
    # the empty slot that follows
    if t > t0 + 2.6:
        pa = ease_out(min(1.0, (t - t0 - 2.6) / 0.6))
        a = int(220 * pa)
        y0 = 1080 + 3 * 132
        d.rounded_rectangle((x0, y0, x0 + w, y0 + 100), radius=26,
                            outline=PERI + (int(a * 0.5),), width=3)
        txt = "NOTHING POSTED SINCE MAY"
        f = _f("body", 32, 600)
        d.text((x0 + (w - _tw(d, txt, f)) // 2, y0 + 34), txt, font=f, fill=PERI + (a,))
    img.alpha_composite(layer)


def dev_postcard(img, t, t0):
    """A published post with its source cited — the turn, on paper."""
    p = spring(min(1.0, (t - t0) / 0.75)) if t > t0 else 0.0
    if p <= 0.01:
        return
    layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    a = int(255 * min(1.0, p * 1.4))
    x0, w = 108, W - 216
    y0 = 1080 + int((1 - p) * 70)
    hgt = 330
    rounded(d, (x0 + 8, y0 + 14, x0 + w + 8, y0 + hgt + 14), 30, fill=(20, 32, 60, int(a * 0.14)))
    rounded(d, (x0, y0, x0 + w, y0 + hgt), 30, fill=PAPER + (a,), outline=NAVY + (int(a * 0.12),), width=3)
    d.ellipse((x0 + 34, y0 + 34, x0 + 108, y0 + 108), fill=NAVY + (a,))
    vf = _f("serif", 46, 600)
    d.text((x0 + 58, y0 + 46), "V", font=vf, fill=PAPER + (a,))
    d.text((x0 + 128, y0 + 44), "Your name", font=_f("body", 38, 700), fill=INK + (a,))
    d.text((x0 + 128, y0 + 86), "Fractional CMO · 8:00 AM", font=_f("body", 28, 400), fill=(148, 160, 181, a))
    qf = _f("serif", 44, 400)
    probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
    quote = "The consultants who win aren't the loudest. They're the ones who never went quiet."
    yy = y0 + 150
    for ln in wrap_words(probe, [{"t": x} for x in quote.split()], qf, w - 80):
        d.text((x0 + 40, yy), " ".join(x["t"] for x in ln), font=qf, fill=INK + (a,))
        yy += 58
    sf = _f("body", 28, 600)
    st = "source: today's news"
    sw = _tw(d, st, sf) + 56
    rounded(d, (x0 + 40, y0 + hgt - 76, x0 + 40 + sw, y0 + hgt - 20), 28, fill=WASH + (a,))
    d.text((x0 + 68, y0 + hgt - 64), st, font=sf, fill=NAVY + (a,))
    img.alpha_composite(layer)


DEVICES = {
    "clocknight": dev_clocknight,
    "clockday": dev_clockday,
    "dots": dev_dots,
    "feed": dev_feed,
    "postcard": dev_postcard,
}
