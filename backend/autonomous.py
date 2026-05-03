import logging
import os
import re
from datetime import datetime
from groq import Groq
from dotenv import load_dotenv

from search import search_industry_news, format_news_context
import linkedin as li
import auth as auth_module
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
HOOK FORMULA — you MUST use one of these for line 1 (nothing else on line 1):
  A) SPECIFIC NUMBER: "[X]% of [audience] [surprising fact]. Not [wrong assumption]. [Real cause]."
  B) NAMED EVENT: "[Company] just [did X]. The implication nobody is talking about: [Y]."
  C) COUNTERINTUITIVE TRUTH: "The [best/smartest] [people] I know [do the opposite of conventional wisdom]."
  D) UNCOMFORTABLE CONFESSION: "I [specific mistake] for [timeframe]. Here's what I missed."
  E) MYTH-BUSTER: "Everyone says [X]. I've seen the opposite."

QUALITY RULES (non-negotiable):
- Hook = line 1, completely alone. Must use formula A-E above. Must contain a real number, company, or specific event.
- Be specific. Name the actual company, report title, or statistic. Vague = ignored. "AI is changing things" = automatic fail.
- Sound like a real expert texting a sharp colleague. Not a newsletter. Not a press release.
- No filler sentences. Every line must add information the previous line did not.
- Hashtags: lowercase, relevant, max 4. Put them on the last line alone.

FORMATTING (critical — posts that break this get buried by the LinkedIn algorithm):
- Every 1-2 sentences = its own paragraph, separated by a blank line.
- NEVER write more than 2 sentences in a row without a blank line.
- Short sentences. If over 20 words, split it.
- The hook must be completely alone on the first line.

ABSOLUTELY BANNED (using any of these is an automatic fail):
possibilities are endless, I couldn’t help but think, it’s amazing to see, the future is bright,
game-changer, dive deep, landscape, leverage, unlock potential, at the end of the day,
in today’s fast-paced world, I read something this week, thrilled to announce, excited to share,
synergy, paradigm shift, move the needle, circle back, learnings, impactful, groundbreaking.

The post must look exactly like this:
[One-line hook using formula A-E — stops the scroll]

[1-2 sentences with a specific fact, name, or example]

[1-2 sentences adding a new point — not restating]

[1-2 sentences of concrete takeaway or implication]

[One sharp closing question specific to this audience]

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
    """
    Returns a post type that varies by weekday AND by how many times this company
    has run today, so repeated Run Now clicks always produce a different type.
    """
    from datetime import date
    import hashlib

    is_personal = company.get("profile_type") == "personal"
    rotation = PERSONAL_ROTATION if is_personal else COMPANY_ROTATION

    # Count how many posts this company has already made today
    today_str = date.today().isoformat()
    company_id = company.get("id", "default")
    log = get_post_log()
    runs_today = sum(
        1 for e in log
        if e.get("company_id") == company_id
        and e.get("timestamp", "").startswith(today_str)
        and e.get("status") in ("posted", "dry_run_fired")
    )

    # Use a hash of (company_id + date + runs_today) to pick from rotation
    # This gives deterministic but varied results across runs
    seed = hashlib.md5(f"{company_id}{today_str}{runs_today}".encode()).hexdigest()
    idx = int(seed[:4], 16) % len(rotation)
    return rotation[idx]


def get_post_type_info(company: dict) -> dict:
    """
    Returns data for the dashboard: the next post type that will be generated,
    and a list of the 3 most recently generated post types.
    """
    next_type_key = _get_post_type(company)
    next_type_label = POST_TYPE_LABELS.get(next_type_key, next_type_key)

    company_id = company.get("id", "default")
    log = get_post_log()
    recent = []
    
    # log is sorted chronologically (oldest to newest usually, or we can reverse it)
    # let's iterate in reverse
    for entry in reversed(log):
        if entry.get("company_id") == company_id and entry.get("status") in ("posted", "dry_run_fired"):
            pt_label = entry.get("post_type", "")
            if pt_label:
                recent.append(pt_label)
            if len(recent) >= 3:
                break

    return {
        "next_post_type": next_type_label,
        "recent_post_types": recent
    }


_REVISION_SYSTEM = """You are a senior LinkedIn editor who has edited 5,000+ viral posts.

You receive a drafted LinkedIn post. Your job: make it impossible to scroll past.

Check every item — fix any that fail:

1. HOOK (line 1 only): Does it use one of these formulas?
   - Specific Number: "[X]% of [audience] [surprising fact]. Not [assumption]. [Reality]."
   - Named Event: "[Company] just [did X]. The implication nobody’s talking about: [Y]."
   - Counterintuitive Truth: "The [best/top] [people] in [field] [do opposite of norm]."
   - Confession: "I [specific mistake] for [timeframe]. Here’s what I missed."
   - Myth-Buster: "Everyone says [X]. I’ve seen the opposite."
   If NOT — rewrite line 1 completely using the best formula for this content.

2. SPECIFICITY: Does every paragraph name a real company, number, or example?
   Replace any vague sentence with something concrete and verifiable.

3. BANNED PHRASES: Remove every instance of:
   game-changer, landscape, unlock, leveraging, synergy, paradigm, thrilled, excited,
   it goes without saying, at the end of the day, move the needle, circle back, learnings, impactful.

4. SENTENCE LENGTH: Split any sentence over 20 words.

5. CLOSING QUESTION: Is it specific to THIS exact audience?
   "What do you think?" = fail. Replace with a question only an expert in this field would ask.

6. FORMAT: Hook alone on line 1. Every paragraph separated by blank lines. Hashtags alone on last line.

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
    industry    = company["industry"]

    # Phase 0: generate a locked, formula-specific hook
    locked_hook = ""
    try:
        from hooks import generate_hook
        context = (news_context or "") + "\n\n" + _build_company_brief(company)[:600]
        allowed = company.get("allowed_hooks", [])
        locked_hook = generate_hook(context=context, industry=industry, post_type=post_type, allowed_hooks=allowed)
    except Exception:
        pass

    # Phase 1: generate the full post body, hook pre-anchored
    prompts  = PERSONAL_PROMPTS if is_personal else COMPANY_PROMPTS
    template = prompts[post_type]
    hook_instruction = (
        f"\n\nSTART YOUR POST WITH THIS EXACT LINE (do not change it, do not add anything before it):\n"
        f"\"{locked_hook}\"\n"
        f"Write the rest of the post to build naturally from this opening."
        if locked_hook else ""
    )
    prompt = template.format(
        name=company["name"],
        industry=industry,
        tone=company.get("tone", "conversational" if is_personal else "professional"),
        news=news_context or "No specific news today — draw from general industry knowledge.",
        company_brief=_build_company_brief(company),
        quality_rules=_PERSONAL_QUALITY_RULES if is_personal else "",
    ) + hook_instruction

    response = _groq.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        max_tokens=1024,
    )
    draft = _format_linkedin_post(response.choices[0].message.content.strip())

    # Enforce locked hook as line 1 if the model drifted
    if locked_hook and draft:
        first_line = draft.split("\n")[0].strip()
        if locked_hook.lower() not in first_line.lower():
            draft = locked_hook + "\n\n" + draft

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
    return bool(company.get("carousel_enabled"))


def run_for_company(company: dict, allow_free_manual: bool = False) -> dict:
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
        gen_info = auth_module.get_gen_info(user_id)
        if gen_info.get("plan") == "free" and not allow_free_manual:
            log_entry["status"] = "skipped"
            log_entry["error"] = "Autonomous posting requires Pro"
            _append_log(log_entry)
            return log_entry

        if gen_info["limit"] != -1 and gen_info["used"] >= gen_info["limit"]:
            log_entry["status"] = "skipped"
            log_entry["error"] = "Free generation limit reached"
            _append_log(log_entry)
            return log_entry

        if not li.is_connected(user_id):
            raise ValueError("LinkedIn not connected for this user")

        NEWS_HEAVY  = ("trend_commentary", "industry_stat", "trend_reaction", "stat_reaction")
        num_results = 6 if post_type in NEWS_HEAVY else 3
        # Pass post_type so search uses targeted queries for this specific post angle
        news_results = search_industry_news(
            company["industry"], company["name"], num_results, post_type=post_type
        )
        news_context = format_news_context(news_results)

        if do_carousel:
            from carousel import generate_carousel_content, render_carousel_pdf
            content   = generate_carousel_content(company, news_context, post_type)
            pdf_bytes = render_carousel_pdf(content, company)
            post_text = content.get("post_text", "")
            log_entry["post_text"] = post_text
            result = li.upload_and_post_carousel(user_id, pdf_bytes, post_text, title=company["name"])
            log_entry["post_urn"] = result.get("id", "")
            log_entry["status"] = "posted"
            auth_module.increment_gens(user_id)
            logger.info(f"[Autonomous] Carousel posted for {company['name']}")
        else:
            post_text = generate_autonomous_post(company, news_context, post_type)
            log_entry["post_text"] = post_text
            result = li.post_to_linkedin(user_id, post_text)
            log_entry["post_urn"] = result.get("id", "")
            log_entry["status"] = "posted"
            auth_module.increment_gens(user_id)
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
