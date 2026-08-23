"""
Generate a music bed for a Voyce reel, synced to that reel's phase timings.

Each reel has four hard beats — the hook card, the cut, the paper rise, the
stamp — and a bed that moves on those beats sounds composed rather than laid
over the top. So this writes a WAV whose harmony is dark and unresolved during
the problem, opens up a fifth exactly when the paper rises, and lands on the
root under the stamp.

Output goes in marketing/audio/. Drop it into Clipchamp or CapCut underneath
the screen recording — the timings already line up, so no nudging required.

Usage:
    python tools/make_reel_bed.py --reel 11pm
    python tools/make_reel_bed.py --all
    python tools/make_reel_bed.py --dur 11.2 --hook 1.8 --turn 5.2 --payoff 8.8
"""

import argparse
import os
import wave

import numpy as np

SR = 44100

# Phase timings mirror the reel factory, so a bed lines up with its visuals.
REELS = {
    "silence":    dict(dur=11.4, hook=1.85, turn=5.4, payoff=9.0),
    "product":    dict(dur=11.8, hook=1.80, turn=3.6, payoff=9.4),
    "price":      dict(dur=11.6, hook=2.00, turn=5.6, payoff=9.2),
    "11pm":       dict(dur=11.2, hook=1.80, turn=5.2, payoff=8.8),
    "quarter":    dict(dur=11.7, hook=1.95, turn=5.8, payoff=9.3),
    "notwriting": dict(dur=11.5, hook=1.90, turn=5.5, payoff=9.1),
}

# A minor — the key the whole account sits in.
A2, E3, A3, C4, E4, G4, A4, C5 = 110.0, 164.81, 220.0, 261.63, 329.63, 392.0, 440.0, 523.25


def tri(t, freq, harmonics=5):
    """Triangle-ish tone: odd harmonics with 1/n^2 rolloff. Softer than a saw,
    and more present than a pure sine on phone speakers."""
    out = np.zeros_like(t)
    for k in range(harmonics):
        n = 2 * k + 1
        out += ((-1) ** k) * np.sin(2 * np.pi * n * freq * t) / (n * n)
    return out * (8 / np.pi ** 2)


def pad(t, notes, t0, t1, gain, attack=0.9, release=1.2):
    """Sustained chord voice with a slow swell and a little detune drift, so it
    breathes instead of sitting dead."""
    out = np.zeros_like(t)
    win = (t >= t0) & (t < t1)
    if not win.any():
        return out
    env = np.clip((t - t0) / attack, 0, 1) * np.clip((t1 - t) / release, 0, 1)
    env = np.where(win, env, 0.0)
    for f in notes:
        drift = 1.0 + 0.0009 * np.sin(2 * np.pi * 0.11 * t)
        out += tri(t, f, 4) * drift * env
    return out * gain


def pluck(t, freq, at, dur, gain):
    """Soft mallet note — the melodic layer that makes this read as music."""
    rel = t - at
    win = (rel >= 0) & (rel < dur)
    if not win.any():
        return np.zeros_like(t)
    env = np.where(win, np.exp(-3.2 * np.clip(rel, 0, None) / dur), 0.0)
    body = tri(t, freq, 5) * 0.8
    shimmer = np.sin(2 * np.pi * freq * 2.01 * t) * 0.22
    return (body + shimmer) * env * gain


def sub(t, at, gain=0.5):
    """Pitch-dropping thump. Bottoms out at 72Hz, not 40 — anything lower just
    disappears on laptop and phone speakers."""
    rel = t - at
    win = (rel >= 0) & (rel < 0.55)
    if not win.any():
        return np.zeros_like(t)
    r = np.clip(rel, 0, None)
    f = 220.0 * np.exp(np.log(72.0 / 220.0) * np.clip(r / 0.2, 0, 1))
    env = np.where(win, np.exp(-7.0 * r), 0.0)
    return np.sin(2 * np.pi * f * r) * env * gain


def swell(t, at, dur, gain, seed=1):
    """Filtered noise rise — the paper coming up out of the navy."""
    rel = t - at
    win = (rel >= 0) & (rel < dur)
    if not win.any():
        return np.zeros_like(t)
    rng = np.random.default_rng(seed)
    noise = rng.standard_normal(t.size)
    # cheap sweeping brightness: blend raw noise against a smoothed copy
    smooth = np.convolve(noise, np.ones(64) / 64, mode="same")
    frac = np.clip(rel / dur, 0, 1)
    bright = noise * frac ** 1.7 + smooth * (1 - frac ** 1.7)
    env = np.where(win, np.sin(np.pi * np.clip(frac, 0, 1)) ** 1.4, 0.0)
    return bright * env * gain


def one_pole_varying(x, cutoff):
    """One-pole lowpass with a time-varying cutoff. This is what carries the
    'opens up on the turn' move — the lift is a timbre change, not just volume."""
    rc = 1.0 / (2 * np.pi * cutoff)
    a = (1.0 / SR) / (rc + 1.0 / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(x.size):
        acc += a[i] * (x[i] - acc)
        y[i] = acc
    return y


def build(dur, hook, turn, payoff, out_path):
    t = np.arange(int(dur * SR)) / SR
    mix = np.zeros_like(t)

    # ── Harmony: unresolved through the problem, a fifth opening on the turn ──
    mix += pad(t, [A2, E3], 0.0, turn + 0.25, 0.115, attack=1.1, release=0.5)
    mix += pad(t, [A2, E3, C4, E4], turn, payoff + 0.4, 0.105, attack=0.55, release=0.7)
    mix += pad(t, [A2, E3, A3], payoff, dur, 0.100, attack=0.5, release=1.5)

    # ── Motif: two hanging notes, then the line resolves as the paper lands ──
    for at, f, g in [(hook + 0.15, A3, 0.16), (hook + 1.30, C4, 0.135), (hook + 2.60, A3, 0.11)]:
        if at < turn - 0.25:
            mix += pluck(t, f, at, 2.0, g)
    for at, f, g in [(turn + 0.30, E4, 0.17), (turn + 0.85, G4, 0.15),
                     (turn + 1.55, A4, 0.135), (turn + 2.45, E4, 0.10)]:
        if at < payoff - 0.25:
            mix += pluck(t, f, at, 1.8, g)
    mix += pluck(t, A4, payoff + 0.55, 2.3, 0.145)
    mix += pluck(t, C5, payoff + 0.55, 2.3, 0.075)

    # ── Hits on the visual beats ──
    mix += swell(t, max(0.0, hook - 0.05), 0.40, 0.085, seed=7)   # the cut
    mix += swell(t, turn - 0.06, 0.78, 0.125, seed=13)            # paper rises
    mix += sub(t, payoff + 0.16, 0.46)                            # stamp lands

    # a slow heartbeat under the problem, so the silence still has a pulse
    beat = 0.625
    at = hook + 0.2
    while at < turn - 0.2:
        mix += sub(t, at, 0.13)
        at += beat * 2

    # ── Tone shaping: darker before the turn, open after, settling on payoff ──
    cutoff = np.where(
        t < turn, 1150.0,
        np.where(
            t < payoff,
            1150.0 + 2100.0 * np.clip((t - turn) / 0.5, 0, 1),
            3250.0 - 1500.0 * np.clip((t - payoff) / 0.6, 0, 1),
        ),
    )
    mix = one_pole_varying(mix, cutoff)

    # ── Seamless loop: short fades so a restart is inaudible ──
    fade = int(0.16 * SR)
    ramp = np.linspace(0, 1, fade)
    mix[:fade] *= ramp
    mix[-fade:] *= ramp[::-1]

    peak = max(1e-9, float(np.max(np.abs(mix))))
    pcm = np.clip(mix * (0.72 / peak), -1.0, 1.0)
    pcm16 = (pcm * 32767).astype("<i2")

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    with wave.open(out_path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm16.tobytes())
    return out_path, t.size / SR, os.path.getsize(out_path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--reel", choices=sorted(REELS), help="use a reel's stored timings")
    ap.add_argument("--all", action="store_true", help="generate a bed for every reel")
    ap.add_argument("--dur", type=float)
    ap.add_argument("--hook", type=float)
    ap.add_argument("--turn", type=float)
    ap.add_argument("--payoff", type=float)
    ap.add_argument("--out")
    a = ap.parse_args()

    jobs = []
    if a.all:
        for name, tm in REELS.items():
            jobs.append((tm, os.path.join("marketing", "audio", f"voyce-bed-{name}.wav")))
    elif a.reel:
        jobs.append((REELS[a.reel], a.out or os.path.join("marketing", "audio", f"voyce-bed-{a.reel}.wav")))
    elif None not in (a.dur, a.hook, a.turn, a.payoff):
        jobs.append((dict(dur=a.dur, hook=a.hook, turn=a.turn, payoff=a.payoff),
                     a.out or os.path.join("marketing", "audio", "voyce-bed-custom.wav")))
    else:
        ap.error("pass --reel NAME, --all, or all of --dur --hook --turn --payoff")

    for tm, out in jobs:
        path, secs, size = build(tm["dur"], tm["hook"], tm["turn"], tm["payoff"], out)
        print(f"wrote {path}  ({secs:.2f}s, {size / 1024:.0f} KB)  "
              f"cut {tm['hook']}s · paper {tm['turn']}s · stamp {tm['payoff']}s")


if __name__ == "__main__":
    main()
