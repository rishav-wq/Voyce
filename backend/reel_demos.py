"""
reel_demos.py — demo/showcase reel specs (the ones that show the real product).

These drive frontend/reel-scene.html, which walks the actual app: Today ->
Create -> LinkedIn preview -> posted, with the screens sliding the way they do
in the product and a cursor doing the clicking.

The research this follows: problem-first framing, never feature-first. The hook
names the viewer's situation, and the product only appears as the answer to it.
A * before a word marks it for the Fraunces italic accent, matching the hero's
"Sound like you."
"""

# Retimed after auditing the render as a contact sheet: roughly 40% of the old
# runtime held a frame with nothing changing, which is exactly where a viewer
# swipes. Nothing now sits still for more than about 2.5s, and the payoff is a
# beat rather than a card someone waits out.
_BASE = dict(
    dur=22.00,
    hookOut=1.90,     # the hook line clears; the app is already moving behind it
    tNav=5.00,        # Create is clicked
    tSource=5.80,     # the source pastes in
    tGenerate=8.60,   # Generate is clicked
    tWrite=9.60,      # slide to the preview, the post writes
    typeDur=3.20,
    tPublish=14.60,   # Post now
    tBack=16.80,      # back to Today, the log entry lands
    payoffAt=19.40,   # 2.6s of payoff, not 4
)

_NEWS = {
    "headline": "LinkedIn changes how reach is measured",
    "source": "reuters.com · 23 min ago",
}

_SOURCE = ("LinkedIn is changing how reach is measured for creator posts, with "
           "sends and watch time weighted above likes.")

_POST = (
    "Reach is being measured differently again.\n\n"
    "The consultants who lose here aren't the ones\n"
    "writing badly. They're the ones who went quiet."
)

DEMOS = {
    # The flagship: the whole loop, the way a user actually walks it.
    "fullflow": {
        **_BASE,
        "kicker": "REAL DEMO",
        "hook": "I didn't write *this post.",
        "profile": {"name": "Rishav — AI agents",
                    "niche": "niche: AI for consultants",
                    "author": "Rishav Raj"},
        "news": _NEWS,
        "source": _SOURCE,
        "post": _POST,
        "logline": "logged: reuters.com · published 8:00 AM",
        # step 0 is the hook itself — it now lives in the caption slot over a
        # product that is already moving, instead of a static card nothing
        # happens on for two seconds.
        "steps": [
            {"k": "h", "at": 0.00,  "cap": "I didn't write *this post.", "big": True},
            {"k": "a", "at": 2.05,  "cap": "Set your niche *once."},
            {"k": "b", "at": 5.00,  "cap": "It finds *today's news."},
            {"k": "c", "at": 9.70,  "cap": "Writes your take, in *your voice."},
            {"k": "d", "at": 14.60, "cap": "One tap. *Published."},
            {"k": "e", "at": 17.10, "cap": "While you were *with a client."},
        ],
        "bed": "voyce-bed-fullflow.wav",
        "caption": (
            "I didn't write this post. Voyce did - while I was with a client.\n\n"
            "Set your niche once. It finds the day's news, writes your take in your "
            "voice, and publishes on schedule. Every post logged with the source it "
            "reacted to. Preview first, pause anytime.\n\n"
            "Comment VOICE and I'll send you the link.\n\n"
            "#saas #aitools #linkedinghostwriter #consultants #buildinpublic"
        ),
    },

    # Same walkthrough, aimed at the objection instead of the workflow.
    "trust": {
        **_BASE,
        "kicker": "THE SCARY PART",
        "hook": "Letting software post *as you.",
        "profile": {"name": "Rishav — AI agents",
                    "niche": "preview mode · pause anytime",
                    "author": "Rishav Raj"},
        "news": _NEWS,
        "source": _SOURCE,
        "post": _POST,
        "logline": "source logged · one-click pause · official LinkedIn API",
        "steps": [
            {"k": "h", "at": 0.00,  "cap": "Letting software post *as you.", "big": True},
            {"k": "a", "at": 2.05,  "cap": "You see the *source it used."},
            {"k": "b", "at": 5.00,  "cap": "And the draft, *before it goes out."},
            {"k": "c", "at": 9.70,  "cap": "Your voice — *not generic AI."},
            {"k": "d", "at": 14.60, "cap": "Post it, or *pause it."},
            {"k": "e", "at": 17.10, "cap": "Every post *logged."},
        ],
        "bed": "voyce-bed-trust.wav",
        "caption": (
            "The scariest part of letting software post as you is not knowing what "
            "it will say.\n\n"
            "So Voyce shows you first. Preview mode, the news source cited on every "
            "post, one-click pause, and the official LinkedIn API - the same door "
            "Buffer uses. No browser bots, no cookies.\n\n"
            "Comment VOICE for the link.\n\n"
            "#saas #trust #linkedintips #consultants #aitools"
        ),
    },
}


def bed_timings(name: str) -> dict:
    """What make_reel_bed needs: the three beats the music has to hit.

    The paper-rise beat the typographic reels use maps here to the moment the
    post starts writing — that is where a demo actually turns.
    """
    d = DEMOS[name]
    return dict(dur=d["dur"], hook=d["hookOut"], turn=d["tWrite"], payoff=d["payoffAt"])
