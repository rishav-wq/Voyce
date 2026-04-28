import logging
import os
import re
from datetime import datetime
from groq import Groq
from dotenv import load_dotenv

from search import search_industry_news, format_news_context
import linkedin as li
import db

load_dotenv()
_groq = Groq(api_key=os.getenv("GROQ_API_KEY"))
logger = logging.getLogger(__name__)

# ── Company post rotation (0=Mon … 6=Sun) ─────────────────────────────────────
COMPANY_ROTATION = {
    0: "trend_commentary",
    1: "expert_insight",
    2: "expert_insight",
    3: "product_spotlight",
    4: "industry_stat",
    5: "case_study",
    6: "expert_insight",
}

# ── Personal brand rotation ────────────────────────────────────────────────────
PERSONAL_ROTATION = {
    0: "trend_reaction",    # Mon — my take on this week's news
    1: "hot_take",          # Tue — bold opinion
    2: "lesson_learned",    # Wed — story from experience
    3: "expert_insight_p",  # Thu — insight from deep in the field
    4: "stat_reaction",     # Fri — surprising number + my read
    5: "personal_story",    # Sat — longer narrative / turning point
    6: "hot_take",          # Sun — end the week with an opinion
}

POST_TYPE_LABELS = {
    # Company
    "trend_commentary":  "Trend Commentary",
    "expert_insight":    "Expert Insight",
    "product_spotlight": "Product Spotlight",
    "industry_stat":     "Industry Stat",
    "case_study":        "Case Study / Story",
    # Personal
    "trend_reaction":    "Trend Reaction",
    "hot_take":          "Hot Take",
    "lesson_learned":    "Lesson Learned",
    "expert_insight_p":  "Expert Insight",
    "stat_reaction":     "Stat Reaction",
    "personal_story":    "Personal Story",
}

# ── Company prompt templates ──────────────────────────────────────────────────
COMPANY_PROMPTS = {

    "trend_commentary": """You are writing a LinkedIn post for {name} ({industry}).

TODAY'S INDUSTRY NEWS:
{news}

COMPANY CONTEXT (use lightly — you are commentating on the trend, not selling):
{company_brief}

Write a Trend Commentary post:
- Hook: reference a specific trend or news item above
- Your take: what does this mean for the industry? (2-3 short paragraphs)
- End with a question that invites discussion
- Hashtags on final line (3-5)
- Blank line between every paragraph
- Tone: {tone} — sounds like a smart human, NOT a press release

Return ONLY the post text.""",

    "expert_insight": """You are writing a LinkedIn post for {name} ({industry}).

COMPANY EXPERTISE:
{company_brief}

INDUSTRY CONTEXT:
{news}

Write an Expert Insight post in this format:
- Hook: a bold or counterintuitive statement about the industry
- Body: 3 short bullet points or paragraphs of genuine insight
  (things only someone deep in {industry} would know)
- CTA: end with "What's your experience with this?" or similar
- Hashtags on final line (3-5)
- Blank line between every section
- Tone: {tone}

Return ONLY the post text.""",

    "product_spotlight": """You are writing a LinkedIn post for {name} ({industry}).

COMPANY KNOWLEDGE BASE:
{company_brief}

Write a Product Spotlight post using this story structure:
- The Problem: one specific pain point {name}'s customers face
- The Old Way: how companies used to deal with it (briefly)
- The Better Way: how {name} solves it (be specific, use real product details)
- The Result: a concrete outcome (use numbers if possible, or qualitative impact)
- End with a soft CTA (not "buy now" — "curious how this works for your team?")
- Hashtags on final line (3-4)
- Blank line between every section
- Tone: {tone} — educational not salesy

Return ONLY the post text.""",

    "industry_stat": """You are writing a LinkedIn post for {name} ({industry}).

TODAY'S NEWS AND DATA:
{news}

COMPANY CONTEXT:
{company_brief}

Write an Industry Stat post:
- Hook: lead with a surprising or striking statistic from the news above
- Explain: why this number matters (1-2 sentences)
- Connect: how this relates to what {name} does or sees in the market
- Insight: what smart companies should do about it
- Hashtags on final line (3-5)
- Blank line between every paragraph
- Tone: {tone}

Return ONLY the post text.""",

    "case_study": """You are writing a LinkedIn post for {name} ({industry}).

COMPANY KNOWLEDGE BASE:
{company_brief}

Write a Case Study / Story post:
- Open with a relatable challenge a customer faced (no real names needed)
- Describe the situation in 2-3 short sentences
- What changed when they used {name}'s approach
- The measurable or qualitative result
- One-line takeaway lesson
- End with: "If your team faces [similar challenge], here's what worked."
- Hashtags on final line (3-4)
- Blank line between every section
- Tone: {tone} — storytelling, human, specific

Return ONLY the post text.""",
}

# ── Personal brand prompt templates ──────────────────────────────────────────
_PERSONAL_QUALITY_RULES = """
QUALITY RULES (non-negotiable):
- No clichés. NEVER write: "possibilities are endless", "I couldn't help but think", "it's amazing to see", "the future is bright", "game-changer", "dive deep", "landscape", "leverage", "unlock potential", "at the end of the day", "in today's fast-paced world", "I read something this week".
- Be specific. Name the actual company, report, number, or trend. Vague = ignored.
- Sound like a real person texting a smart friend, not a newsletter or press release.
- Hook = first line only. It must make someone stop scrolling. Make it surprising, contrarian, or oddly specific.
- No filler sentences. Every line must earn its place.
- Hashtags: lowercase, relevant, max 4. Put them on the last line alone.

LINKEDIN FORMATTING (critical — posts that ignore this get buried):
- Every 1-2 sentences = its own block, separated by a blank line.
- NEVER write more than 2 sentences in a row without a blank line.
- The hook must be completely alone on the first line — nothing else on that line.
- Short sentences. If a sentence is longer than 20 words, split it.

The post must look exactly like this structure:
[One-line hook that stops the scroll]

[1-2 sentences]

[1-2 sentences]

[1-2 sentences]

[One closing question]

#tag1 #tag2 #tag3
"""

PERSONAL_PROMPTS = {

    "trend_reaction": """You are ghostwriting a LinkedIn post for {name}, a personal brand in {industry}.

TODAY'S NEWS:
{news}

AUTHOR CONTEXT:
{company_brief}

{quality_rules}

Write a Trend Reaction post in first person (I, my, I've):

Hook (1 line): Start with the specific thing you read — name the source, stat, or company. NOT "I read something this week." Something like: "Google just said 80% of code will be AI-written by 2027. Here's why that number is misleading."

Body (2-3 short paragraphs):
- What the trend actually means — your honest, specific take
- What most people are getting wrong about it
- One non-obvious implication you see coming

Closing: One sharp question that invites real responses. Not "What do you think?" — something specific: "Are you already seeing this in your team?"

Hashtags on final line.

Return ONLY the post text.""",

    "hot_take": """You are ghostwriting a LinkedIn post for {name}, a personal brand in {industry}.

INDUSTRY CONTEXT:
{news}

AUTHOR CONTEXT:
{company_brief}

{quality_rules}

Write a Hot Take post in first person:

Hook (1 line): A bold opinion that will make someone in {industry} either nod hard or want to argue. State it plainly — no softening.

Body:
- Line 1-2: Why you believe this (your evidence or experience)
- Line 3-4: The common wisdom you're pushing back against
- Line 5-6: What actually works instead

Counter: One sentence: "I know this is controversial because [reason]." Then stand your ground.

Closing line: "Agree or disagree?" — or a more specific version.

Hashtags on final line.

Return ONLY the post text.""",

    "lesson_learned": """You are ghostwriting a LinkedIn post for {name}, a personal brand in {industry}.

AUTHOR CONTEXT:
{company_brief}

{quality_rules}

Write a Lesson Learned post in first person:

Hook (1 line): Start mid-story or with the mistake. E.g. "I spent 6 months building the wrong thing. Here's what I missed." NOT a generic opener.

Story (3-4 short punchy sentences):
- The specific situation (what were you doing, what happened)
- The moment you realised something was wrong
- What you did differently

The lesson (1 sentence, bold idea): State it cleanly. Should be repeatable advice.

Why it matters for anyone in {industry}: 1-2 sentences max.

Closing: "What took you too long to learn?" or a more specific question.

Hashtags on final line.

IMPORTANT: If website context is marked EXTERNAL REFERENCE MATERIAL, draw the story from general {industry} experience instead.

Return ONLY the post text.""",

    "expert_insight_p": """You are ghostwriting a LinkedIn post for {name}, a personal brand in {industry}.

AUTHOR CONTEXT:
{company_brief}

INDUSTRY CONTEXT:
{news}

{quality_rules}

Write an Expert Insight post in first person:

Hook (1 line): A counterintuitive truth. Something that sounds wrong at first but is correct. E.g. "The best {industry} people I know rarely follow best practices."

Insight 1 (2-3 sentences): The pattern you've observed. Specific.
Insight 2 (2-3 sentences): Why the common approach fails. Name the mistake.
Insight 3 (2-3 sentences): What actually works. Your take, not generic advice.

Closing: "Am I the only one seeing this?" or a sharper version targeting your specific audience.

Hashtags on final line.

Return ONLY the post text.""",

    "stat_reaction": """You are ghostwriting a LinkedIn post for {name}, a personal brand in {industry}.

TODAY'S NEWS AND DATA:
{news}

AUTHOR CONTEXT:
{company_brief}

{quality_rules}

Write a Stat Reaction post in first person:

Hook (1 line): Drop the stat immediately — cold, no warm-up. E.g. "43% of AI projects fail at deployment. Not ideation. Deployment." Then one word or phrase: "Let that sink in."

Your read (2 paragraphs):
- What this number actually means (not just restating it)
- What the industry is doing wrong because of this

What to do instead: 2-3 sentences of concrete advice.

Closing: A specific question. Not "what do you think?" — e.g. "Have you seen this play out in your org?"

Hashtags on final line.

Return ONLY the post text.""",

    "personal_story": """You are ghostwriting a LinkedIn post for {name}, a personal brand in {industry}.

AUTHOR CONTEXT:
{company_brief}

{quality_rules}

Write a Personal Story post in first person — narrative style:

Hook (1 line): Drop into the scene mid-action. E.g. "My manager called me into a room and said we were shutting down the project." NOT "I want to tell you a story about..."

Scene (2-3 sentences): Specific details. What were you doing, what was at stake, what was the pressure.

Turning point (2-3 sentences): The decision, the mistake, or the realisation. Be honest.

Outcome (1-2 sentences): What happened. Numbers if possible, honest qualitative if not.

Takeaway (1 sentence): The clean lesson. Should apply to anyone in {industry}.

Closing: A relatable question that connects the story to the reader's experience.

Hashtags on final line.

IMPORTANT: If website context is marked EXTERNAL REFERENCE MATERIAL, invent a plausible career story in {industry} instead.

Return ONLY the post text.""",
}


def _build_company_brief(company: dict) -> str:
    is_personal = company.get("profile_type") == "personal"
    analysis = company.get("analysis", {})

    if is_personal:
        designation = company.get("designation", "").strip()
        author_line = f"Author: {company['name']}"
        if designation:
            author_line += f", {designation}"
        author_line += " (personal brand — write in first person, as if the author is speaking)"
        parts = [
            author_line,
            f"Topics they cover: {company['industry']}",
        ]
        if designation:
            parts.append(f"Their role gives them credibility on: {designation}")
        if analysis.get("description"):
            parts.append(f"Background: {analysis['description']}")
        if analysis.get("key_topics"):
            parts.append(f"Key topics: {', '.join(analysis['key_topics'])}")
    else:
        parts = [
            f"Company: {company['name']}",
            f"What they do: {analysis.get('description', '')}",
            f"Products/Services: {', '.join(analysis.get('products_services', []))}",
            f"Target audience: {analysis.get('target_audience', '')}",
            f"Unique value: {analysis.get('unique_value', '')}",
            f"Key topics: {', '.join(analysis.get('key_topics', []))}",
            f"Content themes: {', '.join(analysis.get('content_themes', []))}",
        ]
    if company.get("website_content"):
        is_external = (company.get("website_type") == "external")
        if is_external:
            parts.append(
                f"\nEXTERNAL REFERENCE MATERIAL (use as inspiration/context to react to — "
                f"do NOT present this as the author's own work or personal experience):\n"
                f"{company['website_content'][:1200]}"
            )
        else:
            parts.append(f"\nAuthor's own website/blog (treat as their personal background and experience):\n{company['website_content'][:1200]}")

    li_analysis = company.get("linkedin_analysis", {})
    if li_analysis:
        parts.append("\n--- LINKEDIN WRITING STYLE (match this voice) ---")
        parts.append(f"Writing style: {li_analysis.get('writing_style', '')}")
        parts.append(f"Tone examples: {'; '.join(li_analysis.get('tone_examples', []))}")
        parts.append(f"Content patterns: {', '.join(li_analysis.get('content_patterns', []))}")
        parts.append(f"Post style summary: {li_analysis.get('post_style_summary', '')}")
        if li_analysis.get("avoid_topics"):
            parts.append(f"Avoid topics: {', '.join(li_analysis['avoid_topics'])}")

    top_posts = company.get("linkedin_top_posts", [])
    if top_posts:
        parts.append("\n--- SAMPLE LINKEDIN POSTS (replicate this voice) ---")
        for i, post in enumerate(top_posts[:3], 1):
            parts.append(f"Example {i}:\n{post[:400]}")

    return "\n".join(p for p in parts if p)


def _get_post_type(company: dict) -> str:
    weekday = datetime.now().weekday()
    if company.get("profile_type") == "personal":
        return PERSONAL_ROTATION[weekday]
    return COMPANY_ROTATION[weekday]


_REVISION_SYSTEM = """You are a senior LinkedIn editor. You will receive a drafted LinkedIn post.

Your job is to rewrite it to be sharper and more engaging — keep the same idea and structure, but:
1. If the hook (first line) is generic or weak, replace it with something specific and impossible to ignore
2. Cut any sentence that is filler, vague, or repeats something already said
3. Replace any clichés with concrete specifics
4. Ensure no paragraph exceeds 2 sentences
5. Make sure the closing question is specific to the audience — not "what do you think?"
6. Keep the hashtags unchanged on the final line

Return ONLY the improved post text — no explanation, no preamble."""


def _revise_post(draft: str, industry: str) -> str:
    """Second-pass revision to sharpen the draft."""
    try:
        resp = _groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _REVISION_SYSTEM},
                {"role": "user", "content": f"Industry: {industry}\n\nDraft post to improve:\n\n{draft}"},
            ],
            max_tokens=1024,
        )
        revised = resp.choices[0].message.content.strip()
        return _format_linkedin_post(revised) if revised else draft
    except Exception:
        return draft  # fall back to original if revision fails


def generate_autonomous_post(company: dict, news_context: str, post_type: str) -> str:
    is_personal = company.get("profile_type") == "personal"
    prompts = PERSONAL_PROMPTS if is_personal else COMPANY_PROMPTS
    template = prompts[post_type]
    prompt = template.format(
        name=company["name"],
        industry=company["industry"],
        tone=company.get("tone", "conversational" if is_personal else "professional"),
        news=news_context or "No specific news today — draw from general industry knowledge.",
        company_brief=_build_company_brief(company),
        quality_rules=_PERSONAL_QUALITY_RULES if is_personal else "",
    )

    response = _groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1024,
    )
    draft = _format_linkedin_post(response.choices[0].message.content.strip())
    return _revise_post(draft, company["industry"])


def _format_linkedin_post(text: str) -> str:
    """Ensure every paragraph is separated by a blank line and no paragraph exceeds 2 sentences."""
    lines = [line.strip() for line in text.splitlines()]
    # Collapse multiple blank lines into one
    collapsed = []
    prev_blank = False
    for line in lines:
        if line == "":
            if not prev_blank:
                collapsed.append("")
            prev_blank = True
        else:
            collapsed.append(line)
            prev_blank = False

    # Join paragraphs and split any that have too many sentences
    paragraphs = "\n".join(collapsed).split("\n\n")
    result = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Skip hashtag line — leave as-is
        if para.startswith("#"):
            result.append(para)
            continue
        # Split sentences and regroup into max-2-sentence blocks
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', para) if s.strip()]
        for i in range(0, len(sentences), 2):
            block = " ".join(sentences[i:i+2])
            result.append(block)

    return "\n\n".join(result)


def _should_post_carousel(company: dict) -> bool:
    """Carousel on Mon/Wed/Fri/Sun, text on Tue/Thu/Sat."""
    if not company.get("carousel_enabled"):
        return False
    return datetime.now().weekday() in {0, 2, 4, 6}  # Mon=0, Wed=2, Fri=4, Sun=6


def run_for_company(company: dict) -> dict:
    company_id = company["id"]
    post_type  = _get_post_type(company)
    do_carousel = _should_post_carousel(company)

    log_entry = {
        "company_id":   company_id,
        "company_name": company["name"],
        "post_type":    POST_TYPE_LABELS.get(post_type, post_type),
        "post_format":  "carousel" if do_carousel else "text",
        "timestamp":    datetime.now().isoformat(),
        "status":       "failed",
        "post_text":    "",
        "error":        "",
    }

    try:
        user_id = company.get("user_id", "")
        if not li.is_connected(user_id):
            raise ValueError("LinkedIn not connected for this user")

        NEWS_HEAVY  = ("trend_commentary", "industry_stat", "trend_reaction", "stat_reaction")
        num_results = 6 if post_type in NEWS_HEAVY else 3
        news_results  = search_industry_news(company["industry"], company["name"], num_results)
        news_context  = format_news_context(news_results)

        if do_carousel:
            from carousel import generate_carousel_content, render_carousel_pdf
            content   = generate_carousel_content(company, news_context, post_type)
            pdf_bytes = render_carousel_pdf(content, company)
            post_text = content.get("post_text", "")
            log_entry["post_text"] = post_text
            result = li.upload_and_post_carousel(user_id, pdf_bytes, post_text, title=company["name"])
            log_entry["post_urn"] = result.get("id", "")
            log_entry["status"] = "posted"
            logger.info(f"[Autonomous] Carousel posted for {company['name']}")
        else:
            post_text = generate_autonomous_post(company, news_context, post_type)
            log_entry["post_text"] = post_text
            result = li.post_to_linkedin(user_id, post_text)
            log_entry["post_urn"] = result.get("id", "")
            log_entry["status"] = "posted"
            logger.info(f"[Autonomous] {POST_TYPE_LABELS[post_type]} posted for {company['name']}")

    except Exception as e:
        log_entry["error"] = str(e)
        logger.error(f"[Autonomous] Failed for {company['name']}: {e}")

    _append_log(log_entry)
    return log_entry


def _append_log(entry: dict):
    db.post_log.insert_one({**entry})


def get_post_log() -> list:
    return list(db.post_log.find({}, {"_id": 0}).sort("timestamp", -1))


def save_post_log(log: list):
    db.post_log.delete_many({})
    if log:
        db.post_log.insert_many([{**e} for e in log])
