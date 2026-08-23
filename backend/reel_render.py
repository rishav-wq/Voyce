"""
reel_render.py — compose Voyce reel frames and encode them to MP4.

reel.py holds the primitives (fonts, easing, grounds, devices); this holds the
timeline and the encoder. Frames are piped straight into ffmpeg and the music bed
is muxed in the same pass, so one call produces a finished, publishable file with
no editor step and no human in the loop.

Timings mirror the designed template: navy hook card -> navy problem -> paper
rises at the turn -> navy payoff, so the loop closes on the frame it opened on.
"""

import os
import shutil
import subprocess

from PIL import Image, ImageDraw

from reel import (
    W, H, FPS, PAPER, INK, NAVY, NAVY_MID, PERI, PERI_SOFT,
    DEVICES, _f, _tw, draw_lockup, draw_words, wrap_words, rounded,
    ease_out, ease_dram, spring_pop,
    ground_navy, ground_paper, vignette_overlay, grain_tile,
)

PAD_X = 82


def _w(text, accent_words=()):
    """'You have a showing-up problem.' -> word dicts, accenting named words."""
    accents = {a.strip(".,!?-").lower() for a in accent_words}
    out = []
    for tok in text.split():
        bare = tok.strip(".,!?-").lower()
        out.append({"t": tok, "accent": bare in accents})
    return out


SPECS = {
    "11pm": dict(
        kicker="STOP DOING THIS", hook="Writing your post at 11pm.",
        hookOut=1.80, turnAt=5.20, payoffAt=8.80, dur=11.20,
        problem=dict(words=_w("Empty composer. Tired take."), device="clocknight", size=88),
        turn=dict(words=_w("Voyce writes at 8am.", ("8am.",)), device="clockday", size=106),
        bed="voyce-bed-11pm.wav",
        caption=(
            "Stop writing your LinkedIn post at 11pm.\n\n"
            "Voyce writes at 8am - while you're with a client. Every post logged with the "
            "news source it reacted to. Preview first, pause anytime.\n\n"
            "Comment VOICE for the link.\n\n"
            "#productivity #consultantlife #linkedintips #automation #fractionalcfo"
        ),
    ),
    "silence": dict(
        kicker="FOR CONSULTANTS", hook="Nobody ever lost a client to a typo.",
        hookOut=1.85, turnAt=5.40, payoffAt=9.00, dur=11.40,
        problem=dict(words=_w("They lost them to silence.", ("silence.",)), device="feed", size=104),
        turn=dict(words=_w("Voyce keeps you in the feed."), device="postcard", size=88),
        bed="voyce-bed-silence.wav",
        caption=(
            "Nobody ever lost a client to a typo. They lost them to silence.\n\n"
            "Voyce finds the day's news in your niche, writes your take in your voice, and "
            "publishes while you're with clients.\n\n"
            "Comment VOICE and I'll send you the link.\n\n"
            "#linkedintips #consultants #fractionalcmo #b2bmarketing #buildinpublic"
        ),
    ),
    "notwriting": dict(
        kicker="THE REAL PROBLEM", hook="You don't have a writing problem.",
        hookOut=1.90, turnAt=5.50, payoffAt=9.10, dur=11.50,
        problem=dict(words=_w("You have a showing-up problem.", ("showing-up",)), device="dots", size=100),
        turn=dict(words=_w("So we removed the decision."), device="postcard", size=88),
        bed="voyce-bed-notwriting.wav",
        caption=(
            "You don't have a writing problem. You have a showing-up problem.\n\n"
            "Every AI tool sells you faster drafts. What actually breaks is the daily "
            "decision to post at all. Voyce removes the decision.\n\n"
            "Comment VOICE and I'll send the link.\n\n"
            "#personalbranding #linkedincontent #consulting #aiwriting #buildinpublic"
        ),
    ),
}


class Renderer:
    """Holds the pre-rendered grounds and overlays so per-frame work stays cheap."""

    def __init__(self):
        self.navy = ground_navy()
        self.paper = ground_paper()
        self.vig = vignette_overlay()
        self.grain = grain_tile()
        self._gw, self._gh = self.grain.size

    def _apply_grain(self, img, i):
        """Tile the noise with a per-frame offset so it shimmers like film."""
        gx = -((i * 37) % self._gw)
        gy = -((i * 53) % self._gh)
        y = gy
        while y < H:
            x = gx
            while x < W:
                img.alpha_composite(self.grain, (x, y))
                x += self._gw
            y += self._gh

    def _hook_card(self, spec, t):
        img = self.navy.copy().convert("RGBA")
        d = ImageDraw.Draw(img)
        d.text((PAD_X, H // 2 - 330), spec["kicker"], font=_f("body", 34, 600), fill=PERI)
        hf = _f("display", 116, 700)
        probe = ImageDraw.Draw(Image.new("RGB", (10, 10)))
        y = H // 2 - 240
        for line in wrap_words(probe, [{"t": x} for x in spec["hook"].split()], hf, W - PAD_X * 2):
            d.text((PAD_X, y), " ".join(x["t"] for x in line), font=hf, fill=PAPER)
            y += 130
        img.alpha_composite(self.vig)
        p = (t - spec["hookOut"]) / 0.42
        if p > 0:
            e = ease_dram(min(1.0, p))
            s = 1.0 + 0.06 * e
            nw, nh = int(W * s), int(H * s)
            big = img.resize((nw, nh), Image.LANCZOS)
            left, top = (nw - W) // 2, (nh - H) // 2
            img = big.crop((left, top, left + W, top + H))
            img.putalpha(max(0, int(255 * (1 - e))))
        return img

    def _payoff(self, spec, t):
        img = self.navy.copy().convert("RGBA")
        d = ImageDraw.Draw(img)
        rel = t - spec["payoffAt"]

        ps = spring_pop(min(1.0, (rel - 0.16) / 0.55)) if rel > 0.16 else 0.0
        if ps > 0.01:
            sf = _f("display", 104, 700)
            txt = "posted."
            tw = _tw(d, txt, sf)
            pw, ph = tw + 150, 196
            stamp = Image.new("RGBA", (pw, ph), (0, 0, 0, 0))
            sd = ImageDraw.Draw(stamp)
            rounded(sd, (0, 0, pw - 1, ph - 1), 34, fill=NAVY_MID + (255,))
            sd.text(((pw - tw) // 2, 44), txt, font=sf, fill=PAPER + (255,))
            scale = 1.0 + (1 - ps) * 1.05
            sw, sh = max(1, int(pw * scale)), max(1, int(ph * scale))
            stamp = stamp.resize((sw, sh), Image.LANCZOS)
            stamp = stamp.rotate(-3, expand=True, resample=Image.BICUBIC)
            fade = min(1.0, ps * 1.6)
            stamp.putalpha(stamp.getchannel("A").point(lambda a: int(a * fade)))
            img.alpha_composite(stamp, ((W - stamp.width) // 2, H // 2 - 360))

        pb = ease_out(min(1.0, (rel - 0.48) / 0.45)) if rel > 0.48 else 0.0
        if pb > 0.01:
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            draw_lockup(ld, W // 2 - 178, H // 2 + 60, 120, on_dark=True)
            layer.putalpha(layer.getchannel("A").point(lambda a: int(a * pb)))
            img.alpha_composite(layer)

        pu = ease_out(min(1.0, (rel - 0.70) / 0.45)) if rel > 0.70 else 0.0
        if pu > 0.01:
            layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
            ld = ImageDraw.Draw(layer)
            uf = _f("display", 40, 600)
            ut = "VOYCE.CO.IN"
            ld.text(((W - _tw(ld, ut, uf)) // 2, H // 2 + 190), ut, font=uf, fill=PERI_SOFT)
            layer.putalpha(layer.getchannel("A").point(lambda a: int(a * pu)))
            img.alpha_composite(layer)

        img.alpha_composite(self.vig)
        img.putalpha(int(255 * ease_out(min(1.0, rel / 0.45))))
        return img

    def frame(self, spec, i):
        t = i / FPS
        base = self.navy.copy().convert("RGBA")

        # the constant brand mark, top-left of every navy frame
        d = ImageDraw.Draw(base)
        draw_lockup(d, PAD_X, 152, 44, on_dark=True)

        pr = spec["problem"]
        draw_words(base, pr["words"], _f("display", pr.get("size", 100), 700),
                   PAD_X, 660, W - PAD_X * 2,
                   t - (spec["hookOut"] + 0.24), color=PAPER, accent=PERI)
        if pr.get("device"):
            DEVICES[pr["device"]](base, t, spec["hookOut"] + 0.74)

        base.alpha_composite(self.vig)

        if t >= spec["turnAt"] - 0.02:
            e = ease_dram(min(1.0, (t - spec["turnAt"]) / 0.72))
            off = int(H * (1 - e))
            panel = self.paper.copy().convert("RGBA")
            pd = ImageDraw.Draw(panel)
            draw_lockup(pd, PAD_X, 152, 44, on_dark=False)
            tn = spec["turn"]
            draw_words(panel, tn["words"], _f("display", tn.get("size", 96), 700),
                       PAD_X, 660, W - PAD_X * 2,
                       t - (spec["turnAt"] + 0.42), color=INK, accent=NAVY)
            if tn.get("device"):
                DEVICES[tn["device"]](panel, t, spec["turnAt"] + 0.50)
            if off > 4:
                sh = Image.new("RGBA", (W, 28), (0, 0, 0, 0))
                sd = ImageDraw.Draw(sh)
                for k in range(28):
                    sd.line((0, k, W, k), fill=(4, 10, 22, int(95 * (1 - k / 28))))
                base.alpha_composite(sh, (0, max(0, off - 28)))
            base.alpha_composite(panel, (0, off))

        if t < spec["hookOut"] + 0.44:
            base.alpha_composite(self._hook_card(spec, t))

        if t >= spec["payoffAt"]:
            base.alpha_composite(self._payoff(spec, t))

        self._apply_grain(base, i // 3)
        return base.convert("RGB")


def _ffmpeg() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        raise RuntimeError("ffmpeg not found. Install it, or pip install imageio-ffmpeg")


def render(name: str, out_path: str, audio_path: str = None, crf: int = 23,
           progress=None) -> str:
    """Render a named reel to a publish-ready MP4, muxing its music bed."""
    if name not in SPECS:
        raise KeyError(f"unknown reel '{name}'. Known: {', '.join(sorted(SPECS))}")
    spec = SPECS[name]
    total = int(spec["dur"] * FPS)
    r = Renderer()

    has_audio = bool(audio_path) and os.path.exists(audio_path)
    cmd = [_ffmpeg(), "-y", "-loglevel", "error",
           "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}",
           "-r", str(FPS), "-i", "-"]
    if has_audio:
        cmd += ["-i", audio_path]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-profile:v", "high", "-level", "4.1",
            "-maxrate", "8M", "-bufsize", "16M", "-movflags", "+faststart"]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-ac", "2", "-ar", "44100", "-shortest"]
    cmd += [out_path]

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    try:
        for i in range(total):
            proc.stdin.write(r.frame(spec, i).tobytes())
            if progress and i % 30 == 0:
                progress(i, total)
    finally:
        try:
            proc.stdin.close()
        except Exception:
            pass
        proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg exited {proc.returncode}")
    return out_path
