import os
import json
import re
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
_groq = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are a world-class LinkedIn ghostwriter and content strategist. Repurpose the given content into platform-optimized posts that get real engagement.

Return ONLY a valid JSON object with exactly these 4 keys. Each value is a single string (or array for twitter_thread).
IMPORTANT: The linkedin_post value must be a single JSON string. Use \\n\\n (escaped newlines) inside the string to separate paragraphs.

{
  "linkedin_post": "...",
  "twitter_thread": ["Tweet 1 [1/4]", "Tweet 2 [2/4]", "Tweet 3 [3/4]", "Tweet 4 [4/4]"],
  "email_snippet": "...",
  "blog_summary": "..."
}

═══ LINKEDIN POST RULES (non-negotiable) ═══

HOOK (first line — the only thing people see before "see more"):
- Must stand completely alone on line 1 — nothing else
- Make it specific, surprising, or contrarian — NOT generic
- Good: "43% of AI projects fail at deployment. Not at ideation. Deployment."
- Bad: "I want to share some thoughts on an important topic."
- Bad: "Have you ever wondered about..."

STRUCTURE:
- Every 1-2 sentences = its own block, separated by a blank line (\\n\\n)
- NEVER more than 2 sentences in a row without a blank line
- Max 5-6 blocks total — short posts outperform long ones
- End with ONE closing question that invites a specific response
- Hashtags on the very last line alone (3-4 max, lowercase)

WRITING STYLE:
- Write like a smart person texting a colleague — not a press release
- Be specific: use real numbers, company names, or concrete examples from the content
- Every sentence must earn its place — cut filler ruthlessly
- Short sentences win. If a sentence exceeds 20 words, split it.

NEVER USE: "game-changer", "landscape", "unlock", "dive deep", "revolutionize",
"in today's world", "I'm excited", "this is huge", "let that sink in" (overused),
"the future of X is here", "at the end of the day", "it goes without saying",
"I couldn't help but", "thrilled to share", "it's no secret that"

═══ TWITTER THREAD RULES ═══
- Tweet 1: Hook — the single sharpest point, written to earn a click-through
- Tweets 2-3: Expand with specific evidence, data, or examples from the content
- Tweet 4: Actionable takeaway + soft CTA
- Each tweet max 260 chars (leave room for the [X/4] counter)
- No hashtags in threads — they kill engagement

═══ EMAIL SNIPPET RULES ═══
- 100-120 words, conversational tone
- Lead with the most interesting fact or tension from the content
- One clear takeaway in the last sentence
- End with [READ MORE →]

═══ BLOG SUMMARY RULES ═══
- 150-180 words
- Opens with a question or bold statement that frames why this matters now
- Previews 2-3 specific things the reader will learn
- Ends with a transition sentence into the full post"""


_POLISH_PROMPT = """You are a ruthless LinkedIn editor. You will receive a JSON object with a linkedin_post field.

Your job: rewrite ONLY the linkedin_post field to make it sharper.

Rules:
- Keep the structure and core idea — just improve the writing
- Replace any weak or generic opener with something specific and arresting
- Cut any sentence that doesn't add new information
- Replace any clichés or vague claims with concrete specifics from the original content
- Ensure every paragraph is 1-2 sentences max, separated by blank lines
- The hook (first line) must stand alone and be impossible to ignore
- Return the SAME JSON structure with only linkedin_post rewritten — all other fields unchanged"""


def generate_content(raw_text: str) -> dict:
    # Pass 1: generate
    response = _groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Content to repurpose:\n\n{raw_text[:3000]}"},
        ],
        response_format={"type": "json_object"},
        max_tokens=2048,
    )
    draft = json.loads(response.choices[0].message.content)

    # Pass 2: polish the LinkedIn post
    try:
        polish_resp = _groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _POLISH_PROMPT},
                {"role": "user", "content": f"Original content:\n{raw_text[:1500]}\n\nDraft to improve:\n{json.dumps(draft)}"},
            ],
            response_format={"type": "json_object"},
            max_tokens=1024,
        )
        polished = json.loads(polish_resp.choices[0].message.content)
        if polished.get("linkedin_post"):
            draft["linkedin_post"] = polished["linkedin_post"]
    except Exception:
        pass  # fall back to draft if polish fails

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
