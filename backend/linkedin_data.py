import csv
import io
import json
import os
import re
import zipfile

import pdfplumber
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))


# ── PDF Parser ────────────────────────────────────────────────────────────────

def parse_linkedin_pdf(file_bytes: bytes) -> dict:
    """Parse a LinkedIn profile PDF downloaded from LinkedIn."""
    text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text += (page.extract_text() or "") + "\n"

    return {
        "type": "pdf",
        "raw_text": text[:6000],
        "analysis": _analyze_linkedin_data(text[:6000], "profile PDF"),
    }


# ── ZIP Archive Parser ────────────────────────────────────────────────────────

def parse_linkedin_zip(file_bytes: bytes) -> dict:
    """
    Parse LinkedIn data export ZIP.
    Key files: Posts.csv, Shares.csv, Profile.csv, Connections.csv
    """
    extracted = {}

    with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
        names = z.namelist()

        # Posts / Shares
        posts = _read_csv_from_zip(z, names, ["Posts.csv", "Shares.csv"])
        if posts:
            post_texts = []
            for row in posts[:30]:  # top 30 posts
                text = row.get("ShareCommentary") or row.get("Share Commentary") or \
                       row.get("Content") or row.get("Message") or ""
                if text.strip():
                    post_texts.append(text.strip())
            extracted["posts"] = post_texts

        # Profile
        profile = _read_csv_from_zip(z, names, ["Profile.csv"])
        if profile:
            extracted["profile"] = profile[0] if profile else {}

        # About / Summary
        about = _read_csv_from_zip(z, names, ["About.csv"])
        if about:
            extracted["about"] = about

    combined_text = _build_combined_text(extracted)
    return {
        "type": "zip",
        "posts_found": len(extracted.get("posts", [])),
        "raw_text": combined_text[:6000],
        "analysis": _analyze_linkedin_data(combined_text, "data archive"),
        "top_posts": extracted.get("posts", [])[:10],
    }


def _read_csv_from_zip(z: zipfile.ZipFile, names: list, candidates: list) -> list[dict]:
    for candidate in candidates:
        # Try exact match and case-insensitive match
        match = next((n for n in names if n.lower().endswith(candidate.lower())), None)
        if match:
            try:
                with z.open(match) as f:
                    content = f.read().decode("utf-8", errors="ignore")
                    reader = csv.DictReader(io.StringIO(content))
                    return [row for row in reader]
            except Exception:
                continue
    return []


def _build_combined_text(extracted: dict) -> str:
    parts = []

    if extracted.get("profile"):
        profile = extracted["profile"]
        parts.append(f"Name: {profile.get('First Name', '')} {profile.get('Last Name', '')}")
        parts.append(f"Headline: {profile.get('Headline', '')}")
        parts.append(f"Summary: {profile.get('Summary', '')}")

    if extracted.get("posts"):
        parts.append("\n--- RECENT LINKEDIN POSTS ---")
        for i, post in enumerate(extracted["posts"][:20], 1):
            parts.append(f"\nPost {i}:\n{post}")

    return "\n".join(parts)


# ── AI Analysis ───────────────────────────────────────────────────────────────

def _analyze_linkedin_data(text: str, source: str) -> dict:
    prompt = f"""Analyze this LinkedIn {source} data and extract insights for content strategy.

{text}

Return a JSON object with these exact keys:
{{
  "writing_style": "description of how they write (formal/casual/storytelling/data-driven)",
  "top_topics": ["topic 1", "topic 2", "topic 3", "topic 4", "topic 5"],
  "tone_examples": ["example phrase 1", "example phrase 2"],
  "content_patterns": ["pattern they use often e.g. lists, questions, stories"],
  "audience_focus": "who they seem to write for",
  "avoid_topics": ["topics they never touch or seem to avoid"],
  "post_style_summary": "2 sentence summary of their LinkedIn content style"
}}

Return ONLY valid JSON, no explanation."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.2,
    )

    text_out = response.choices[0].message.content.strip()
    match = re.search(r'\{.*\}', text_out, re.DOTALL)
    if match:
        text_out = match.group(0)
    try:
        return json.loads(text_out)
    except Exception:
        return {}


# ── Unified entry point ───────────────────────────────────────────────────────

def parse_linkedin_upload(filename: str, file_bytes: bytes) -> dict:
    if filename.lower().endswith(".pdf"):
        return parse_linkedin_pdf(file_bytes)
    elif filename.lower().endswith(".zip"):
        return parse_linkedin_zip(file_bytes)
    else:
        raise ValueError("Unsupported file type. Upload a LinkedIn PDF profile or ZIP data export.")
