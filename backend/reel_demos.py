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

# Rebuilt on the five beats a demo is supposed to run: hook, problem, solution,
# proof, action. The old cut ran hook and solution and spent the saved seconds
# on more solution, which is the one mistake every framework warns about by
# name. The solution beat is 4s shorter than it was; the problem and proof
# beats are what that bought.
_BASE = dict(
    dur=23.00,
    hookOut=1.90,     # the hook line clears
    tProblem=2.10,    # their empty composer gets named
    tDays=3.40,       # and dated -- the number is the beat, not the picture
    tApp=4.40,        # only now does the product appear
    tNav=6.40,        # Create is clicked
    tSource=7.00,     # the source pastes in
    tGenerate=8.60,   # Generate is clicked
    tWrite=9.50,      # slide to the preview, the post resolves
    typeDur=2.10,     # the scramble, not a typewriter
    tPublish=13.60,   # Post now
    tBack=15.10,      # back to Today
    proofAt=16.10,    # the receipt: logged, with its source
    payoffAt=19.00,   # the card, and the ask
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
        "before": {
            "name": "Rishav Raj",
            "sub": "Consultant \u00b7 AI for consultants",
            "ph": "Start a post\u2026",
            "pre": "Your last post \u00b7 ",
            "count": 23,
            "post": " days ago",
        },
        "steps": [
            {"k": "h",  "at": 0.00,  "cap": "I didn't write *this post.", "big": True},
            {"k": "p1", "at": 2.10,  "cap": "You know you should *post."},
            {"k": "p2", "at": 3.45,  "cap": "Your last one was *23 days ago."},
            {"k": "a",  "at": 5.30,  "cap": "So: set your niche *once."},
            {"k": "b",  "at": 7.00,  "cap": "It finds *today's news."},
            {"k": "c",  "at": 10.40, "cap": "Writes your take, in *your voice."},
            {"k": "d",  "at": 13.60, "cap": "One tap. *Published."},
            {"k": "e",  "at": 15.30, "cap": "While you were *with a client."},
            {"k": "f",  "at": 16.30, "cap": "Logged, with the *source it used."},
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
        "before": {
            "name": "Rishav Raj",
            "sub": "Consultant \u00b7 AI for consultants",
            "ph": "Scheduled \u2014 posting as you at 8:00 AM\u2026",
            "pre": "Sent as you this week \u00b7 ",
            "count": 3,
            "post": " you never saw",
        },
        "steps": [
            {"k": "h",  "at": 0.00,  "cap": "Letting software post *as you.", "big": True},
            {"k": "p1", "at": 2.10,  "cap": "The scary part isn't *the writing."},
            {"k": "p2", "at": 3.45,  "cap": "It's not knowing *what it will say."},
            {"k": "a",  "at": 5.30,  "cap": "So you see the *source it used."},
            {"k": "b",  "at": 7.00,  "cap": "And the draft, *before it goes out."},
            {"k": "c",  "at": 10.40, "cap": "Your voice -- *not generic AI."},
            {"k": "d",  "at": 13.60, "cap": "Post it, or *pause it."},
            {"k": "e",  "at": 15.30, "cap": "Official LinkedIn API. *No bots."},
            {"k": "f",  "at": 16.30, "cap": "Every post *logged."},
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
