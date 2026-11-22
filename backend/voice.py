"""Voice answers (22 Nov 2026): the weekly question answered by talking instead of typing.

Senior consultants skip a typing box but will talk for a minute; what they say carries the names, numbers and moments
that make a post theirs (the 21 Nov content audit traced generic posts to missing personal material). The browser
records a short clip; ElevenLabs' speech-to-text (Scribe) turns it into text, which lands in the answer box for the
author to check before saving, so nothing is stored that they haven't seen.

Config: ELEVENLABS_API_KEY. ELEVENLABS_STT_MODEL (default scribe_v2; scribe_v1 is tried when it fails). With no key,
transcribe() raises VoiceUnavailable and the page keeps the typing box.
"""
import logging
import os

import requests

MAX_BYTES = 6 * 1024 * 1024     # about five minutes of browser audio (opus); answers run 30-90 seconds
MIN_BYTES = 2 * 1024            # less is a tap, not an answer
_URL = "https://api.elevenlabs.io/v1/speech-to-text"


class VoiceUnavailable(Exception):
    """No key, or the service didn't answer: the author types instead."""


class VoiceRejected(Exception):
    """The clip itself is the problem (empty, too long, silent): say so."""


def _key() -> str:
    return (os.getenv("ELEVENLABS_API_KEY") or os.getenv("ELEVEN_LABS_API_KEY") or "").strip().strip('"').strip("'")


def available() -> bool:
    return bool(_key())


def transcribe(audio: bytes, filename: str = "answer.webm", mime: str = "audio/webm") -> str:
    """The clip as text, or VoiceRejected / VoiceUnavailable."""
    if not audio or len(audio) < MIN_BYTES:
        raise VoiceRejected("That recording was too short. Hold the button and talk for a few seconds.")
    if len(audio) > MAX_BYTES:
        raise VoiceRejected("That recording is too long. Keep it under three minutes.")
    key = _key()
    if not key:
        raise VoiceUnavailable("Voice answers aren't switched on yet. Type your answer instead.")
    models = [os.getenv("ELEVENLABS_STT_MODEL", "scribe_v2").strip() or "scribe_v2"]
    if "scribe_v1" not in models:
        models.append("scribe_v1")
    last = ""
    for model in models:
        try:
            r = requests.post(_URL, headers={"xi-api-key": key},
                              data={"model_id": model, "tag_audio_events": "false"},
                              files={"file": (filename or "answer.webm", audio, mime or "audio/webm")}, timeout=90)
        except requests.RequestException as e:
            last = str(e)[:200]
            continue
        if r.ok:
            text = " ".join(str((r.json() or {}).get("text") or "").split()).strip()
            if len(text.split()) < 3:
                raise VoiceRejected("Voyce couldn't hear any words in that. Try again a little closer to the mic.")
            return text
        last = f"{r.status_code} {r.text[:200]}"
        if r.status_code in (400, 413, 422) and "model" not in r.text.lower():
            break   # the clip, not the model: another model won't help
    logging.warning(f"[Voice] transcription failed: {last}")
    raise VoiceUnavailable("Voyce couldn't turn that into text just now. Type your answer, or try again in a minute.")
