"""Reimplements reel-fx scrambleTo exactly, so we can read every frame of noise
the reel will ever show and check none of it spells something we would not want
on a brand video. Deterministic by construction, which is the only reason this
check is possible at all."""
import sys
sys.path.insert(0, "backend")
from reel_demos import DEMOS

CHARS = "abcdefghijklmnopqrstuvwxyz"
BAD = {"ass", "fuck", "shit", "cunt", "dick", "cock", "piss", "twat", "slut",
       "rape", "nazi", "kill", "die", "dead", "hell", "damn", "crap", "sex",
       "fag", "wank", "turd", "arse", "bitch", "whore", "porn", "anal"}


def imul(a, b):
    r = (a * b) & 0xFFFFFFFF
    return r - 0x100000000 if r >= 0x80000000 else r


def scramble(text, p, dur, speed=26, band=0.22, salt=1):
    n = len(text)
    tick = int(p * dur * speed)
    out = []
    for i, c in enumerate(text):
        if c in " \n":
            out.append(c); continue
        s0 = (0 if n < 2 else i / n) * (1 - band)
        if p >= s0 + band:
            out.append(c)
        elif p <= s0:
            out.append(" ")
        else:
            k = (imul(i, 73856093) ^ imul(tick, 19349663) ^ salt) & 0xFFFFFFFF
            k = (k ^ (k >> 13)) & 0xFFFFFFFF
            out.append(CHARS[k % 26])
    return "".join(out)


for name in ("fullflow", "trust"):
    d = DEMOS[name]
    dur, fps = d["typeDur"], 60
    hits, frames = [], 0
    for f in range(int(dur * fps) + 1):
        p = min(1.0, f / (dur * fps))
        s = scramble(d["post"], p, dur)
        frames += 1
        low = s.lower()
        for w in BAD:
            idx = 0
            while True:
                idx = low.find(w, idx)
                if idx < 0:
                    break
                before = low[idx - 1] if idx else " "
                after = low[idx + len(w)] if idx + len(w) < len(low) else " "
                if not before.isalpha() and not after.isalpha():
                    hits.append((round(f / fps, 3), w, s.strip()[:72]))
                idx += 1
    print(f"{name}: {frames} frames of noise, {len(hits)} unwanted word(s)")
    for h in hits[:10]:
        print(f"   t+{h[0]}s  '{h[1]}'  ...{h[2]}")
