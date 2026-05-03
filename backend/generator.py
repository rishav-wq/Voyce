import os
import json
import re
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
_groq = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are a world-class LinkedIn ghostwriter. Your posts get 10x more engagement because you never write generic content.

You repurpose content into platform-optimised posts that stop the scroll.

Return ONLY a valid JSON object with exactly these 4 keys:
{
  "linkedin_post": "...",
  "twitter_thread": ["Tweet 1 [1/4]", "Tweet 2 [2/4]", "Tweet 3 [3/4]", "Tweet 4 [4/4]"],
  "email_snippet": "...",
  "blog_summary": "..."
}

═══ LINKEDIN POST ═══

CHOOSE ONE of these proven hook formulas based on the content:
  A) SPECIFIC NUMBER: "[X]% of [audience] [surprising fact]. Not [wrong assumption]. [Real cause]."
  B) NAMED EVENT: "[Company] just [did X]. The implication nobody is talking about: [Y]."
  C) COUNTERINTUITIVE TRUTH: "The [best/smartest] [people] in [field] [do the opposite of what everyone expects]."
  D) UNCOMFORTABLE CONFESSION: "I [specific mistake] for [timeframe]. Here is what I missed."
  E) MYTH-BUSTER: "Everyone says [X]. The numbers say the opposite."

HOOK RULES (line 1 — the ONLY thing visible before “see more”):
- Must be ONE of the formulas above
- Must be completely alone on line 1 — nothing else
- Must contain a specific fact, number, company, or event from the content
- Must create tension, surprise, or contradiction

STRUCTURE:
- Hook line alone
- [blank line]
- 1-2 sentences that BUILD on the hook (specific, not vague)
- [blank line]
- 1-2 sentences that ADD a new specific point (data, example, implication)
- [blank line]
- 1-2 sentences of concrete takeaway or action
- [blank line]
- One closing question that ONLY this specific audience would care about
- [blank line]
- 3-4 lowercase hashtags alone on the last line

WRITING RULES:
- Write like a senior practitioner texting a sharp colleague — not a press release
- Name real companies, real reports, real numbers from the content
- Never fabricate first-person experience. Do not write "I built / I invested / I used / my team"
  unless that exact claim appears in the provided content/context.
- When using third-party material, attribute it explicitly ("According to X...", "Y reported...").
- Every sentence earns its place. No filler. No transitions like "Furthermore" or "In conclusion"
- Max 20 words per sentence. If longer, split it.
- \\n\\n between every paragraph (use escaped newlines in the JSON string)

ABSOLUTELY BANNED PHRASES: game-changer, landscape, unlock, dive deep, revolutionize,
in today’s world, I’m excited, this is huge, the future of X is here, at the end of the day,
it goes without saying, I couldn’t help but, thrilled to share, it’s no secret that,
leveraging, synergy, paradigm shift, move the needle, circle back, learnings, impactful.

═══ TWITTER THREAD ═══
- Tweet 1: The hook — single sharpest point, use formula A or E from above
- Tweets 2-3: Specific evidence, data, named examples from the content
- Tweet 4: Actionable takeaway + soft CTA
- Max 260 chars each (leave room for the [X/4] counter)
- No hashtags in threads

═══ EMAIL SNIPPET ═══
- 100-120 words, conversational
- Lead with the most interesting fact or tension from the content
- One clear takeaway in the last sentence
- End with [READ MORE →]

═══ BLOG SUMMARY ═══
- 150-180 words
- Opens with a question or bold statement that frames why this matters now
- Previews 2-3 specific things the reader will learn
- Ends with a transition sentence into the full post"""


_POLISH_PROMPT = """You are a ruthless LinkedIn editor. You receive a JSON object with a linkedin_post field.

Your ONLY job: make the linkedin_post impossible to scroll past.

Check each of these — fix any that fail:
1. Hook (line 1): Is it using a scroll-stopping formula (Specific Number / Named Event / Counterintuitive Truth / Confession / Myth-Buster)? If it’s generic or warm-up text, replace it entirely.
2. Specificity: Does every paragraph name a real company, number, or example? Replace any vague sentence with something verifiable.
3. Banned phrases: Remove any instance of: game-changer, landscape, unlock, leveraging, synergy, paradigm, thrilled, excited, it goes without saying, at the end of the day.
4. Sentence length: Split any sentence over 20 words.
5. Closing question: Is it specific to THIS audience? Generic questions like “what do you think?” must be replaced.
6. Format: Every paragraph separated by \\n\\n. Hook alone on line 1. Hashtags alone on last line.

Return the SAME JSON structure — only linkedin_post rewritten. All other fields unchanged."""


def generate_content(raw_text: str, company: dict = None) -> dict:
    # Phase 0: generate a locked scroll-stopping hook first
    locked_hook = ""
    try:
        from hooks import generate_hook
        allowed = company.get("allowed_hooks", []) if company else []
        locked_hook = generate_hook(context=raw_text[:1200], industry="general", allowed_hooks=allowed)
    except Exception:
        pass

    # Build hook constraint for the main prompt
    hook_constraint = (
        f"\n\nCRITICAL: The linkedin_post MUST start with this exact line (do not change it):\n"
        f"\"{locked_hook}\"\n"
        f"Write the rest of the post to follow from this hook naturally."
        if locked_hook else ""
    )

    # Phase 1: generate full content
    response = _groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Content to repurpose:\n\n{raw_text[:3000]}{hook_constraint}"},
        ],
        response_format={"type": "json_object"},
        max_tokens=2048,
    )
    draft = json.loads(response.choices[0].message.content)

    # Ensure locked hook is actually line 1 (in case model drifted)
    if locked_hook and draft.get("linkedin_post"):
        post = draft["linkedin_post"]
        first_line = post.split("\\n")[0].strip()
        if locked_hook.lower() not in first_line.lower():
            draft["linkedin_post"] = locked_hook + "\\n\\n" + post

    # Phase 2: polish — improve body, never touch line 1
    try:
        polish_resp = _groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _POLISH_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Original content:\n{raw_text[:1500]}\n\n"
                        f"Draft to improve:\n{json.dumps(draft)}\n\n"
                        + (f"LOCKED HOOK (do NOT change line 1): \"{locked_hook}\"" if locked_hook else "")
                    ),
                },
            ],
            response_format={"type": "json_object"},
            max_tokens=1024,
        )
        polished = json.loads(polish_resp.choices[0].message.content)
        if polished.get("linkedin_post"):
            draft["linkedin_post"] = polished["linkedin_post"]
    except Exception:
        pass

    return draft


def _fix_json_strings(text: str) -> str:
    """Walk the JSON char-by-char and escape control chars inside string values."""
    result = []
    in_string = False
    escape = False
    for ch in text:
        if escape:
            result.append(ch)
            escape = False
        elif ch == '\\' and in_string:
            result.append(ch)
            escape = True
        elif ch == '"':
            in_string = not in_string
            result.append(ch)
        elif in_string and ch == '\n':
            result.append('\\n')
        elif in_string and ch == '\r':
            result.append('\\r')
        elif in_string and ch == '\t':
            result.append('\\t')
        elif in_string and ord(ch) < 0x20:
            pass  # drop other control chars
        else:
            result.append(ch)
    return ''.join(result)
