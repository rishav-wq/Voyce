import os
import json
import re
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
_groq = Groq(api_key=os.getenv("GROQ_API_KEY"))

SYSTEM_PROMPT = """You are an expert content marketing strategist. Repurpose the given content into platform-optimized posts.

Return ONLY a valid JSON object with exactly these 4 keys. Each value is a single string (or array for twitter_thread).

IMPORTANT: The linkedin_post value must be a single JSON string. Use \\n\\n (escaped newlines) inside the string to separate paragraphs — do NOT create multiple JSON properties for the LinkedIn post.

{
  "linkedin_post": "Hook line alone.\\n\\nFirst point — 1-2 sentences max.\\n\\nSecond point — 1-2 sentences max.\\n\\nCTA sentence.\\n\\n#hashtag1 #hashtag2 #hashtag3",
  "twitter_thread": ["Tweet 1 — hook (max 280 chars) [1/4]", "Tweet 2 [2/4]", "Tweet 3 [3/4]", "Tweet 4 — CTA [4/4]"],
  "email_snippet": "100-150 word newsletter paragraph. Conversational, one clear takeaway. Ends with [READ MORE].",
  "blog_summary": "150-200 word blog intro. Sets context, explains why it matters, previews what the reader will learn."
}

Rules:
- linkedin_post: MUST be one single string with \\n\\n between every 1-2 sentence block
- Match the tone of the original content
- Make each output feel native to its platform
- Never copy-paste — always rewrite for the platform"""


def generate_content(raw_text: str) -> dict:
    response = _groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Content to repurpose:\n\n{raw_text}"},
        ],
        response_format={"type": "json_object"},
        max_tokens=2048,
    )
    return json.loads(response.choices[0].message.content)


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
