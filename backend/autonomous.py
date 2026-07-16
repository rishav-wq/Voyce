import logging
import re
from datetime import datetime
from dotenv import load_dotenv

from llm import generate as llm_generate
from generator import HUMANIZE_PROMPT, _strip_markdown
from search import search_industry_news, format_news_context
import linkedin as li
import auth as auth_module
import db

load_dotenv()
logger = logging.getLogger(__name__)

# ── Post type pools (picked pseudo-randomly per company per day) ──────────────
COMPANY_ROTATION = {
    0: "trend_commentary",
    1: "expert_insight",
    2: "how_to_playbook",
    3: "product_spotlight",
    4: "industry_stat",
    5: "case_study",
    6: "expert_insight",
    7: "myth_vs_reality",
    8: "teardown",
    9: "prediction",
}

PERSONAL_ROTATION = {
    0: "trend_reaction",
    1: "hot_take",
    2: "lesson_learned",
    3: "expert_insight_p",
    4: "stat_reaction",
    5: "personal_story",
    6: "how_to",
    7: "listicle",
    8: "teardown",
    9: "prediction",
    10: "open_question",
}

POST_TYPE_LABELS = {
    # Company
    "trend_commentary":  "Trend Commentary",
    "expert_insight":    "Expert Insight",
    "product_spotlight": "Product Spotlight",
    "industry_stat":     "Industry Stat",
    "case_study":        "Case Study / Story",
    "how_to_playbook":   "Playbook / How-To",
    "myth_vs_reality":   "Myth vs Reality",
    # Personal
    "trend_reaction":    "Trend Reaction",
    "hot_take":          "Hot Take",
    "lesson_learned":    "Lesson Learned",
    "expert_insight_p":  "Expert Insight",
    "stat_reaction":     "Stat Reaction",
    "personal_story":    "Personal Story",
    "how_to":            "How-To / Playbook",
    "listicle":          "Listicle",
    # Shared
    "teardown":          "Teardown",
    "prediction":        "Prediction",
    "open_question":     "Open Question",
}

# One-line, plain-English explanations shown in the dashboard (keyed by LABEL, since
# the API and post log both speak labels).
POST_TYPE_DESCRIPTIONS = {
    "Trend Commentary":  "your take on a real industry news item",
    "Expert Insight":    "a counterintuitive truth only an insider would know",
    "Product Spotlight": "a customer problem and how you solve it, told as a story",
    "Industry Stat":     "a striking statistic and what it really means",
    "Case Study / Story": "a customer challenge, what changed, and the result",
    "Playbook / How-To": "concrete steps to solve one painful problem",
    "Myth vs Reality":   "a belief most people hold, debunked with evidence",
    "Trend Reaction":    "your honest take on today's industry news",
    "Hot Take":          "a bold opinion that makes readers nod or argue",
    "Lesson Learned":    "a mistake or realisation and what it taught you",
    "Stat Reaction":     "a surprising number and your read on it",
    "Personal Story":    "a real moment told as a short narrative",
    "How-To / Playbook": "your method for solving one specific problem",
    "Listicle":          "a tight, save-worthy list of specific insights",
    "Teardown":          "a sharp breakdown of a real company's move",
    "Prediction":        "a bold, checkable call about where things are heading",
    "Open Question":     "a genuine dilemma posed to start a discussion",
}

# ── Hook style hints (from the profile's allowed_hooks setting) ───────────────
_HOOK_STYLE_HINTS = {
    "specific_number": "leading with a striking, specific number or percentage",
    "named_event":     "reacting to a named company, report, or event",
    "counterintuitive": "stating a counterintuitive truth that sounds wrong until explained",
    "confession":      "opening with an honest confession or a mistake",
    "myth_buster":     "challenging a belief most people in the field hold",
    "unexpected_comparison": "drawing an unexpected comparison ('X is like Y') that reframes the topic",
}


def _hook_guidance(allowed_hooks: list | None) -> str:
    hints = [_HOOK_STYLE_HINTS[k] for k in (allowed_hooks or []) if k in _HOOK_STYLE_HINTS]
    if hints:
        return ("\nOPENING LINE: open the post by " + ", or by ".join(hints) +
                ". The first line must be specific and impossible to ignore — no warm-up text.")
    return ("\nOPENING LINE: the first line must be specific and impossible to ignore — "
            "a real number, a named company or event, an honest confession, or a claim that "
            "sounds wrong until explained. No warm-up text.")

# ── Company prompt templates ──────────────────────────────────────────────────
COMPANY_PROMPTS = {

    "trend_commentary": """You are writing a LinkedIn post for {name} ({industry}).

TODAY'S INDUSTRY NEWS:
{news}

COMPANY CONTEXT (use lightly — you are commentating on the trend, not selling):
{company_brief}

Write a Trend Commentary post:
- Open with the specific trend or news item above — name it
- Your take: what does this actually mean for the industry? (2-3 short paragraphs, one clear opinion)
- End with a takeaway or a question only people in this industry would care about — no generic engagement bait
- Hashtags: 0-3 lowercase on the final line, only if genuinely relevant
- Blank line between every paragraph; vary sentence length — not everything short and punchy
- Tone: {tone} — sounds like a smart human, NOT a press release

Return ONLY the post text.""",

    "expert_insight": """You are writing a LinkedIn post for {name} ({industry}).

COMPANY EXPERTISE:
{company_brief}

INDUSTRY CONTEXT:
{news}

Write an Expert Insight post:
- Open with a bold or counterintuitive statement about the industry
- Body: 2-3 paragraphs of genuine insight — things only someone deep in {industry} would know
- End on the strongest point or a specific question — never "What's your experience with this?"
- Hashtags: 0-3 lowercase on the final line, only if genuinely relevant
- Blank line between every section; vary sentence rhythm
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
- Hashtags: 0-3 lowercase on the final line, only if genuinely relevant
- Blank line between every section
- Tone: {tone} — educational not salesy

Return ONLY the post text.""",

    "industry_stat": """You are writing a LinkedIn post for {name} ({industry}).

TODAY'S NEWS AND DATA:
{news}

COMPANY CONTEXT:
{company_brief}

Write an Industry Stat post:
- Lead with a surprising or striking statistic from the news above — name the source
- Explain: why this number matters (1-2 sentences)
- Connect: how this relates to what {name} does or sees in the market
- Insight: what smart companies should do about it
- Hashtags: 0-3 lowercase on the final line, only if genuinely relevant
- Blank line between every paragraph; vary sentence rhythm
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
- Hashtags: 0-3 lowercase on the final line, only if genuinely relevant
- Blank line between every section
- Tone: {tone} — storytelling, human, specific

Return ONLY the post text.""",

    "how_to_playbook": """You are writing a LinkedIn post for {name} ({industry}).

COMPANY KNOWLEDGE BASE:
{company_brief}

INDUSTRY CONTEXT:
{news}

Write a Playbook / How-To post:
- Open with the specific, painful problem this playbook solves — not a generic pain point
- Give 3-5 concrete steps. Each step: what to do + one practical detail that shows real expertise
- Every step must be something a reader could start this week — no "align stakeholders" fluff
- Close with the result a team can expect if they actually follow it
- Hashtags: 0-3 lowercase on the final line, only if genuinely relevant
- Blank line between every step; vary sentence rhythm
- Tone: {tone} — practitioner sharing a working method, not a consultant selling one

Return ONLY the post text.""",

    "myth_vs_reality": """You are writing a LinkedIn post for {name} ({industry}).

COMPANY KNOWLEDGE BASE:
{company_brief}

INDUSTRY CONTEXT:
{news}

Write a Myth vs Reality post:
- Open with a belief most people in {industry} genuinely hold — state it the way believers say it
- Show why it's wrong with real evidence: a number, a named example, or a pattern from the company's experience
- Give the reality, and what to do differently because of it
- Don't strawman — the myth must be something reasonable people actually believe
- Hashtags: 0-3 lowercase on the final line, only if genuinely relevant
- Blank line between every paragraph; vary sentence rhythm
- Tone: {tone}

Return ONLY the post text.""",

    "teardown": """You are writing a LinkedIn post for {name} ({industry}).

COMPANY CONTEXT (use lightly — the analysis is the star, not the company):
{company_brief}

INDUSTRY NEWS AND EXAMPLES:
{news}

Write a Teardown post — a sharp breakdown of one specific, real thing a NAMED company did:
- Pick ONE concrete artifact or move from the news/industry knowledge: a pricing page, a launch,
  an onboarding flow, an ad campaign, a product decision. Name the company in the first line.
- Break down 3-4 specific observations: what they did, why it works (or fails), what most
  companies get wrong on the same thing
- Each observation must be concrete enough that the reader can go look at it themselves
- Close with the transferable lesson for {industry} teams
- Only analyze publicly observable things — never claim insider knowledge
- Hashtags: 0-3 lowercase on the final line, only if genuinely relevant
- Blank line between observations; vary sentence rhythm
- Tone: {tone} — analyst dissecting, not fan praising

Return ONLY the post text.""",

    "prediction": """You are writing a LinkedIn post for {name} ({industry}).

COMPANY CONTEXT:
{company_brief}

INDUSTRY NEWS AND SIGNALS:
{news}

Write a Prediction post:
- Open with ONE bold, specific, time-stamped prediction about {industry} (e.g. "By mid-2027, ...").
  It must be falsifiable — a claim you could check on that date
- Build the reasoning chain: 2-3 current signals (from the news or known industry facts) that
  point to it, each named and specific
- Address the obvious counterargument in one sentence — then hold the position
- Close with what smart companies should do NOW if this is right
- Hashtags: 0-3 lowercase on the final line, only if genuinely relevant
- Blank line between paragraphs; vary sentence rhythm
- Tone: {tone} — confident, not breathless

Return ONLY the post text.""",
}

# ── Shared quality rules ──────────────────────────────────────────────────────
_PERSONAL_QUALITY_RULES = """
QUALITY RULES (non-negotiable):
- Line 1 is the ONLY thing visible before "...see more". HARD RULE: max 60 characters,
  self-contained, an open loop the reader needs closed. Short + tension stops the scroll.
  Write it the way THIS author would say it, not like a marketer.
- If voice examples are provided in AUTHOR CONTEXT, match their rhythm, vocabulary, and
  quirks exactly — voice outranks every other rule.
- Be specific. Name the actual company, report, or statistic. "AI is changing things" = fail.
- Never fabricate personal history. Do NOT claim the author did/used/built/invested in
  something unless that is explicitly in AUTHOR CONTEXT.
- NO STORY WITHOUT A REAL STORY: if AUTHOR CONTEXT contains no lived events for this topic,
  do NOT write a first-person narrative — no invented timelines, dashboards, user counts,
  launches, or pivots. Write an opinion, observation, or teaching post instead. A fabricated
  "here's what happened to me" post published under the author's real name is the worst
  possible failure of this system.
- If evidence is from external reference material, attribute it ("X reports...", "Y's case
  shows...") instead of "I did...".
- FACTUAL SAFETY: never invent exact statistics, product version numbers ("DeepSeek-V3.2",
  "GPT-5.1"), dates, or named studies just to sound authoritative. Use only facts from the
  provided context or things you're genuinely confident are real. When unsure, hedge
  ("models like DeepSeek", "most teams") rather than fabricate a specific a reader can falsify.
- Vary your rhythm: mix short punchy lines with longer natural sentences. A post where
  every sentence is under 10 words reads as AI-generated and gets scrolled past.
- PLAIN ENGLISH, never a whitepaper: write the way a smart person talks. Use contractions
  (I'm, don't, that's). No corporate/academic register: programmatically, systemically,
  mimics, "information density", furthermore, "Ultimately,", "it is worth noting".
- SIMPLICITY IS A HARD RULE: every word one a 15-year-old uses daily ("use" not "utilize").
  Most sentences under 12 words. If a sentence needs a second read, rewrite it.
- LENGTH — HARD DEFAULT: 60-120 words total. Past 150 only when every extra line carries a
  fact. When in doubt, the shorter version ships. Never end on a self-summary or a restated
  hook — end on the strongest sentence.
- HOLD ATTENTION (dwell time is a confirmed reach signal): one idea per line, whitespace,
  and a shape that pulls the eye down — a narrative arc, a numbered list, or a before→after.
- SAVE-WORTHY: build the post so a reader wants to keep it — a usable framework, a numbered
  list of specifics, a checklist, or a clear before→after. A save drives far more reach than
  a like, so make the post reference-worthy, not just agreeable.
- NEVER invent how a platform or algorithm works: no made-up thresholds, mechanisms, or
  rules. If a mechanism isn't in the provided context, state the idea generally or drop it.
- End however fits the post: a takeaway, a sharp specific question, or just the last point
  landing. NEVER directive engagement-bait ("Comment YES", "Tag someone", "Agree?",
  "Repost if…") — LinkedIn auto-suppresses it. One genuine open question is fine.
- No promotional URLs in the body — off-platform links drag reach.
- Hashtags: 0-3 lowercase on the last line, only if genuinely relevant. None is fine.

FORMATTING:
- Blank line between paragraphs. No paragraph over 3 sentences.
- The opening line stands completely alone on line 1.
- PLAIN TEXT ONLY — LinkedIn renders markdown literally. No **bold**, no * or - bullets.
  For list items use "→ " or numbers ("1. ").

NEVER USE (instant AI tells — automatic fail):
- "It's not about X. It's about Y." or any "It's not X — it's Y" construction
- "Here's the thing:" / "Let that sink in" / "Read that again" / "The result?"
- Three-item lists in every paragraph (the rule-of-three tic)
- Starting consecutive paragraphs with the same word
- possibilities are endless, I couldn't help but think, it's amazing to see, the future is
  bright, game-changer, dive deep, landscape, leverage, unlock potential, at the end of the
  day, in today's fast-paced world, I read something this week, thrilled to announce,
  excited to share, synergy, paradigm shift, move the needle, circle back, learnings,
  impactful, groundbreaking.
- Em-dashes (—) or en-dashes (–): the single biggest AI tell. Use commas, periods, or
  parentheses. A plain hyphen is fine only inside a number range (e.g. 85-90%).
"""

_COMPANY_QUALITY_RULES = _PERSONAL_QUALITY_RULES.replace("AUTHOR CONTEXT", "COMPANY CONTEXT")

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

Closing: end on the strongest version of your claim, or a question that splits the room in a specific way — never a bare "Agree or disagree?"

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
Never invent personal events ("I spent...", "I built...", "I invested...") unless present in AUTHOR CONTEXT.

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

Return ONLY the post text.""",

    "stat_reaction": """You are ghostwriting a LinkedIn post for {name}, a personal brand in {industry}.

TODAY'S NEWS AND DATA:
{news}

AUTHOR CONTEXT:
{company_brief}

{quality_rules}

Write a Stat Reaction post in first person:

Hook (1 line): Drop the stat immediately — cold, no warm-up. E.g. "43% of AI projects fail at deployment. Not ideation. Deployment."

Your read (2 paragraphs):
- What this number actually means (not just restating it)
- What the industry is doing wrong because of this

What to do instead: 2-3 sentences of concrete advice.

Closing: A specific question. Not "what do you think?" — e.g. "Have you seen this play out in your org?"

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

IMPORTANT: If website context is marked EXTERNAL REFERENCE MATERIAL, do NOT invent personal stories.
Write an analysis-led post using attributed third-party examples ("Klarna showed...", "Report X found..."),
and only use first-person claims if they are explicitly present in AUTHOR CONTEXT.

Return ONLY the post text.""",

    "how_to": """You are ghostwriting a LinkedIn post for {name}, a personal brand in {industry}.

AUTHOR CONTEXT:
{company_brief}

INDUSTRY CONTEXT:
{news}

{quality_rules}

Write a How-To / Playbook post in first person:

Opening (1 line): the specific, painful problem this solves — stated the way someone living it would say it.

The method (3-5 steps): each step is concrete enough to start this week. Include one practical
detail per step that only a practitioner would know. No "align with stakeholders" fluff.

Close: what changes when you do this — a concrete outcome, not a platitude.

IMPORTANT: Only frame this as "my process" / "what I do" if AUTHOR CONTEXT supports it.
Otherwise frame it as "the approach that works" with attributed examples.

Return ONLY the post text.""",

    "listicle": """You are ghostwriting a LinkedIn post for {name}, a personal brand in {industry}.

AUTHOR CONTEXT:
{company_brief}

INDUSTRY CONTEXT:
{news}

{quality_rules}

Write a Listicle post in first person:

Opening (1 line): what the list delivers and why it's worth reading — specific, with a number
(e.g. "5 things I'd tell anyone starting in {industry} today").

The list (4-6 items): each item is one tight insight — a sentence or two. Every item must be
specific enough that a generic version of it would be obviously worse. Cut any item that
could appear in anyone's list.

Close: the one item that matters most, called out — or what ignoring this list costs.

IMPORTANT: Only use first-person claims supported by AUTHOR CONTEXT; attribute anything external.

Return ONLY the post text.""",

    "teardown": """You are ghostwriting a LinkedIn post for {name}, a personal brand in {industry}.

AUTHOR CONTEXT:
{company_brief}

INDUSTRY NEWS AND EXAMPLES:
{news}

{quality_rules}

Write a Teardown post in first person — a sharp analysis of one real, NAMED company's move:

Opening (1 line): name the company and the specific thing being torn down — a pricing page,
a launch, an onboarding flow, a product decision. Make the reader curious why it matters.

The breakdown (3-4 observations): what they did, why it works or fails, what everyone else
gets wrong on the same thing. Each observation specific enough to verify by looking.

Close: the transferable lesson — what someone in {industry} should steal (or avoid).

IMPORTANT: Analyze only publicly observable things. "X's pricing page does something clever"
is fine; "when I talked to their team" is fabrication unless it's in AUTHOR CONTEXT.

Return ONLY the post text.""",

    "prediction": """You are ghostwriting a LinkedIn post for {name}, a personal brand in {industry}.

AUTHOR CONTEXT:
{company_brief}

INDUSTRY NEWS AND SIGNALS:
{news}

{quality_rules}

Write a Prediction post in first person:

Opening (1 line): ONE bold, specific, time-stamped prediction (e.g. "By mid-2027, ...").
Falsifiable — something readers could check on that date.

Reasoning (2-3 short paragraphs): the current signals pointing to it — named companies,
real numbers, observable shifts from the news or known industry facts.

The counterargument: name the obvious objection in one sentence, then hold the position.

Close: what to do now if this is right — or an invitation to bookmark and check back.

Return ONLY the post text.""",

    "open_question": """You are ghostwriting a LinkedIn post for {name}, a personal brand in {industry}.

AUTHOR CONTEXT:
{company_brief}

INDUSTRY CONTEXT:
{news}

{quality_rules}

Write an Open Question post in first person — SHORT (60-120 words total):

Frame a genuine dilemma the {industry} community is split on — something with real arguments
on both sides, drawn from the news or a recurring debate in the field.

Structure:
- 1-2 lines setting up the dilemma with a specific detail that proves it's a real situation
- One line for each side of the argument — steelman both, don't pick a winner
- End with the question itself, asked plainly. The post's whole job is to start a comment thread.

The tone is genuinely undecided — NOT a rhetorical question with an obvious answer.
No hashtags on this one. No closing summary. The question is the last line.

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
        if company.get("tone_shift") and company.get("tone"):
            parts.append(f"\n--- REAL POSTS BY THIS AUTHOR (keep their vocabulary and quirks, but "
                         f"they have asked to shift their register toward '{company['tone']}') ---")
        else:
            parts.append("\n--- REAL POSTS BY THIS AUTHOR (match this voice exactly — rhythm, vocabulary, quirks) ---")
        for i, post in enumerate(top_posts[:5], 1):
            parts.append(f"Example {i}:\n{post[:600]}")

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


def get_week_plan(company: dict, days: int = 7) -> list[str]:
    """Predict the post type for today + the next N-1 days using the same hash
    the daily runner uses (assuming the first run of each day)."""
    from datetime import date, timedelta
    import hashlib

    is_personal = company.get("profile_type") == "personal"
    rotation = PERSONAL_ROTATION if is_personal else COMPANY_ROTATION
    company_id = company.get("id", "default")
    plan = []
    for i in range(days):
        day = (date.today() + timedelta(days=i)).isoformat()
        seed = hashlib.md5(f"{company_id}{day}0".encode()).hexdigest()
        idx = int(seed[:4], 16) % len(rotation)
        key = rotation[idx]
        plan.append(POST_TYPE_LABELS.get(key, key))
    return plan


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
        "next_post_type_desc": POST_TYPE_DESCRIPTIONS.get(next_type_label, ""),
        "recent_post_types": recent
    }


def _revise_post(draft: str, industry: str) -> str:
    """Humanizer pass: strip AI tells, vary rhythm, keep voice and facts."""
    try:
        revised = llm_generate(
            f"Industry: {industry}\n\nPost to edit:\n\n{draft}",
            system=HUMANIZE_PROMPT,
            max_tokens=1024,
            temperature=0.6,
        )
        return _format_linkedin_post(revised) if revised else draft
    except Exception:
        return draft  # fall back to original if revision fails


def generate_autonomous_post(company: dict, news_context: str, post_type: str) -> str:
    is_personal = company.get("profile_type") == "personal"
    industry    = company["industry"]

    prompts  = PERSONAL_PROMPTS if is_personal else COMPANY_PROMPTS
    template = prompts[post_type]
    prompt = template.format(
        name=company["name"],
        industry=industry,
        tone=company.get("tone", "conversational" if is_personal else "professional"),
        news=news_context or "No specific news today — draw from general industry knowledge.",
        company_brief=_build_company_brief(company),
        quality_rules=_PERSONAL_QUALITY_RULES if is_personal else "",
    )
    if not is_personal:
        prompt += "\n" + _COMPANY_QUALITY_RULES
    prompt += _hook_guidance(company.get("allowed_hooks"))

    draft = _format_linkedin_post(
        llm_generate(prompt, max_tokens=1024, temperature=0.85)
    )
    return _revise_post(draft, industry)


def _format_linkedin_post(text: str) -> str:
    """Normalize spacing and break up only walls of text (4+ sentences in one paragraph)."""
    # LinkedIn renders markdown literally — strip it to plain text
    text = _strip_markdown(text)
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

    paragraphs = "\n".join(collapsed).split("\n\n")
    result = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Skip hashtag and list lines — leave as-is
        if para.startswith("#") or re.match(r"^\s*(\d+[\.\)]|[-•→])", para):
            result.append(para)
            continue
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', para) if s.strip()]
        if len(sentences) <= 3:
            result.append(para)
        else:
            # Wall of text — split into blocks of 2-3 sentences
            for i in range(0, len(sentences), 3):
                result.append(" ".join(sentences[i:i+3]))

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
