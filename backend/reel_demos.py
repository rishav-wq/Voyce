"""
reel_demos.py — demo/showcase reel specs (the ones that show the real product).

These drive frontend/reel-scene.html. Unlike the typographic reels, the whole
point here is that the viewer watches the application do the thing: news lands,
the post writes itself, publish turns green, the source gets logged.

The research this follows: problem-first framing, never feature-first. The hook
names the viewer's situation, and the product only appears as the answer to it.
"""

# Beats every demo shares. Kept explicit rather than computed so the music bed
# (which is generated against hookOut / turn / payoff) stays in step.
_BASE = dict(
    dur=24.0,
    hookOut=2.20,
    tNews=6.40,
    tWrite=10.60,
    typeDur=3.40,
    tPublish=16.40,
    payoffAt=20.60,
    accent="#8fa5d6",
)

DEMOS = {
    # The flagship: the entire loop, start to finish.
    "fullflow": {
        **_BASE,
        "kicker": "REAL DEMO",
        "hook": "I didn't write this post.",
        "profile": {"name": "Rishav — AI agents", "niche": "niche: AI for consultants · posts 8:00 AM"},
        "news": {"headline": "LinkedIn changes how reach is measured",
                 "source": "reuters.com · 23 min ago"},
        "post": ("Reach is being measured differently again.\n\n"
                 "The consultants who lose here aren't the ones\n"
                 "writing badly. They're the ones who went quiet."),
        "logline": "logged: reuters.com · published 8:00 AM",
        "steps": [
            {"k": "a", "at": 2.30,  "cap": "Set your niche *once."},
            {"k": "b", "at": 6.40,  "cap": "It finds *today's news."},
            {"k": "c", "at": 10.60, "cap": "Writes your take, in *your voice."},
            {"k": "d", "at": 16.40, "cap": "And *publishes it."},
            {"k": "e", "at": 18.60, "cap": "While you're *with a client."},
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

    # Same machinery, pointed at the objection instead of the workflow.
    "trust": {
        **_BASE,
        "dur": 23.0,
        "payoffAt": 19.6,
        "tPublish": 15.8,
        "kicker": "THE SCARY PART",
        "hook": "Letting software post as you.",
        "profile": {"name": "Rishav — AI agents", "niche": "preview mode · pause anytime"},
        "news": {"headline": "LinkedIn changes how reach is measured",
                 "source": "reuters.com · 23 min ago"},
        "post": ("Reach is being measured differently again.\n\n"
                 "Worth knowing before you plan next quarter's\n"
                 "content: silence costs more than it used to."),
        "logline": "source logged · one-click pause · official LinkedIn API",
        "steps": [
            {"k": "a", "at": 2.30,  "cap": "You see it *before it goes out."},
            {"k": "b", "at": 6.40,  "cap": "The news it reacted to, *cited."},
            {"k": "c", "at": 10.60, "cap": "Your voice — *not generic AI."},
            {"k": "d", "at": 15.80, "cap": "Publish, or *pause it."},
            {"k": "e", "at": 17.80, "cap": "*You stay in control."},
        ],
        "bed": "voyce-bed-trust.wav",
        "caption": (
            "The scariest part of letting software post as you is not knowing what it "
            "will say.\n\nSo Voyce shows you first. Preview mode, the news source cited "
            "on every post, one-click pause, and the official LinkedIn API - the same "
            "door Buffer uses. No browser bots, no cookies.\n\n"
            "Comment VOICE for the link.\n\n"
            "#saas #trust #linkedintips #consultants #aitools"
        ),
    },
}


def bed_timings(name: str) -> dict:
    """What make_reel_bed needs: the three beats the music has to hit.

    The paper-rise beat the typographic reels use maps here to the moment the
    product starts writing — that's where a demo actually turns.
    """
    d = DEMOS[name]
    return dict(dur=d["dur"], hook=d["hookOut"], turn=d["tWrite"], payoff=d["payoffAt"])
