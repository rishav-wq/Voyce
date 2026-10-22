import logging
import os
import re
from datetime import datetime, date, timedelta
from dotenv import load_dotenv

from llm import generate as llm_generate
from generator import HUMANIZE_PROMPT, _strip_markdown, drop_author_furniture
import analytics
import notes as notes_mod
from search import search_industry_news, format_news_context
import linkedin as li
import auth as auth_module
import db
import notify

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
- End on the takeaway, stated plainly. A question only if it's one only people in this industry would care about — no generic engagement bait
- No hashtags
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
- No hashtags
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
- No hashtags
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
- No hashtags
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
- No hashtags
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
- No hashtags
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
- No hashtags
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
- No hashtags
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
- No hashtags
- Blank line between paragraphs; vary sentence rhythm
- Tone: {tone} — confident, not breathless

Return ONLY the post text.""",
}

# ── Shared quality rules ──────────────────────────────────────────────────────
_PERSONAL_QUALITY_RULES = """
QUALITY RULES (non-negotiable):
- Line 1 is the ONLY thing visible before "...see more". HARD RULE: at most ~90 characters,
  self-contained: a plain statement, a result or a story moment (a specific number helps),
  an open loop the reader needs closed. Never a question: question openers do worst.
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
- FACTUAL SAFETY (HARD BAN — this is the #1 credibility killer): never state a specific,
  checkable fact unless it appears verbatim in the AUTHOR CONTEXT / news provided. In particular:
  - NO financial figures about named companies ("Elastic grew 20%", "trades at a 6x multiple",
    "raised $40M") unless that exact figure is in the provided news.
  - NO round invented percentages ("92% of SaaS startups run on AI", "70% of teams") — these
    read as made up and a reader will call them out. Say "most" / "a growing share" instead.
  - NO product versions, dates, or named studies you can't source.
  A single checkable stat that turns out wrong destroys the whole post's credibility, on the
  author's REAL profile. When in doubt, drop the number and make the point qualitatively.
- Vary your rhythm: mix short punchy lines with longer natural sentences. A post where
  every sentence is under 10 words reads as AI-generated and gets scrolled past.
- PLAIN ENGLISH, never a whitepaper: write the way a smart person talks. Use contractions
  (I'm, don't, that's). No corporate/academic register: programmatically, systemically,
  mimics, "information density", furthermore, "Ultimately,", "it is worth noting".
- SIMPLICITY IS A HARD RULE: every word one a 15-year-old uses daily ("use" not "utilize").
  Most sentences under 12 words. If a sentence needs a second read, rewrite it.
- LENGTH: aim for 130-260 words (about 800-1,600 characters), the range where posts do
  best, and let the material set it. A real process, story or set of numbers fills it; one
  sharp point can stop near 100 words. Never pad: a line that adds no fact, step, example or
  turn gets cut. Past 300 words only for a step-by-step process. Never summarize yourself at
  the end or restate the hook as a closer.
- HOLD ATTENTION (dwell time is a confirmed reach signal): one idea per line, whitespace,
  and a shape that pulls the eye down — a narrative arc, a numbered list, or a before→after.
- SAVE-WORTHY: build the post so a reader wants to keep it — a usable framework, a numbered
  list of specifics, a checklist, or a clear before→after. A save drives far more reach than
  a like, so make the post reference-worthy, not just agreeable.
- NEVER invent how a platform or algorithm works: no made-up thresholds, mechanisms, or
  rules. If a mechanism isn't in the provided context, state the idea generally or drop it.
- EVERY STAT NAMES ITS SOURCE in the post body ("per LinkedIn's 2026 report...") or gets cut.
  An unverifiable number reads as fabricated and burns the author's credibility.
- SELF-PROMOTION BAN (default): never pitch, name, or link the author's own product, tool, or
  service, and never CTA readers to try/buy/DM for access — even if AUTHOR CONTEXT mentions
  what they're building — UNLESS the context explicitly marks THIS post as a product
  reveal/launch post. Repeated product mentions train both readers and the feed algorithm to
  ignore the account; the author's product may inform their perspective, never the post's ask.
- END ON A STATEMENT by default: the takeaway, the sharpest claim, or the last point
  landing. Top posts almost never close on a question. Ask one only when it's specific and
  the post has earned it (it asks for something readers actually know and want to share),
  never as a reflex. NEVER directive engagement-bait ("Comment YES", "Tag someone",
  "Agree?", "Repost if…") — LinkedIn auto-suppresses it.
- No promotional URLs in the body — off-platform links drag reach.
- No hashtags: top posts don't use them and they add no reach. (Only if the author's own
  example posts use them, follow the author.)

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
- Opening with "Most [people / teams / CFOs / founders]...": the most overused hook shape.
- Relentless staccato: every sentence a one-line punch. Mix short punches with natural,
  longer sentences the way people actually talk.
- possibilities are endless, I couldn't help but think, it's amazing to see, the future is
  bright, game-changer, dive deep, landscape, leverage, unlock potential, at the end of the
  day, in today's fast-paced world, I read something this week, thrilled to announce,
  excited to share, synergy, paradigm shift, move the needle, circle back, learnings,
  impactful, groundbreaking, revolutionize, revolutionary, unleash, supercharge, 10x,
  "this will change everything", "went viral" as a boast.
- Em-dashes (—) or en-dashes (–): the single biggest AI tell. Use commas, periods, or
  parentheses. A plain hyphen is fine only inside a number range (e.g. 85-90%).

THE BAR (what readers reward — aim every post at it):
- One real datapoint: a specific fact from TODAY'S NEWS or the provided context, never invented.
- One honest opinion: a take a reasonable peer could disagree with, not a platitude.
- One lived detail: ONLY if AUTHOR CONTEXT supplies one (an experience, a project, a number
  from their work). NEVER fabricate personal experience — if context has none, skip the
  lived detail rather than invent it.
- THE NOVELTY TEST: the post must teach a sharp reader ONE thing they likely don't already
  know — a fresh development, a specific number, a named mechanism, a counterintuitive but
  supported claim. If every sentence states what the reader already believes, the post is
  filler: find the most non-obvious point in the news and build the post around IT.
- NEVER STACK TRUISMS: two consensus statements in a row ("focus beats volume", "consistency
  is key", "quality over quantity") is the worst possible output. One consensus line is
  allowed only as setup for a specific, sourced, or contrarian payoff.
"""

_COMPANY_QUALITY_RULES = _PERSONAL_QUALITY_RULES.replace("AUTHOR CONTEXT", "COMPANY CONTEXT") + """
COMPANY-PAGE EXCEPTION to the self-promotion ban: a post whose instructions explicitly make it
a Product Spotlight IS the sanctioned product post — write it proudly there. The ban still
applies to every other post type: educational posts teach, they never pivot into a pitch."""

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

Closing: end on your sharpest line: the implication, or what you'd do about it. A question only if it's this specific: "Are you already seeing this in your team?"

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

Closing: end on the strongest version of your claim. Never a bare "Agree or disagree?"

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

Closing: end on the lesson, said so someone could repeat it.

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

Closing: end on what this means for someone in {industry}: the sharpest line in the post.

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

Closing: end on what the number changes, said plainly. A question only if it's this specific: "Have you seen this play out in your org?"

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

Closing: end on the takeaway.

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

    # When the merged product view is in play, anchor the subject explicitly —
    # the voice sections below still describe the company/author.
    product_line = ""
    if company.get("product_name"):
        product_line = (
            f"PRODUCT FOCUS FOR THIS POST: {company['product_name']} — keep the "
            f"author's usual voice, but anchor the subject matter in this "
            f"product's niche and audience."
        )

    if is_personal:
        designation = company.get("designation", "").strip()
        author_line = f"Author: {company['name']}"
        if designation:
            author_line += f", {designation}"
        author_line += " (personal brand — write in first person, as if the author is speaking)"
        parts = [
            author_line,
            f"Topics they cover: {company.get('all_topics') or company['industry']}",
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
    if company.get("all_topics"):
        parts.append(f"Today's post is about ONE of their topics: {company['industry']}. Keep it to that topic.")
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
        # Without this the daily posts lifted an example's hook and its story as
        # if new ("my last 7 posts bombed", every day, as a carousel).
        parts.append("HOW TO USE THESE EXAMPLES: copy the rhythm, vocabulary and tone only. NEVER reuse "
                     "their hooks, opening lines or phrases, and never retell their stories, numbers or "
                     "experiences as if they just happened. Leave out any series title, episode number "
                     "or sign-off they put on their own posts (\"X NOTES | #5\", \"Day 12 of...\"): those "
                     "belong to the posts they write, and a number you add is made up. Don't sign off "
                     "with their name or title.")

    if (company.get("knowledge") or "").strip():
        parts.append(
            "\n--- KNOWLEDGE BASE (ground every claim in these facts/rules; never "
            "contradict them or invent around them) ---\n" + company["knowledge"].strip()[:2000])
    if (company.get("profile_facts") or "").strip():
        parts.append(
            "\n--- THEIR CAREER (from their LinkedIn profile; real facts you may draw on when they fit. "
            "Never embellish them or add scenes, feelings or results they don't state) ---\n"
            + company["profile_facts"].strip()[:8000])
    told = notes_mod.record_block(company)
    if told:
        parts.append("\n--- " + told)

    return "\n".join(p for p in [product_line, *parts] if p)


_STOP = {"the", "a", "an", "is", "are", "to", "of", "and", "in", "on", "for", "it", "this", "that", "be", "with"}


def _opening_words(text: str) -> set[str]:
    """The first line's words, numbers folded together ("my last 7" = "my last 12")."""
    first = next((ln for ln in (text or "").splitlines() if ln.strip()), "")
    first = re.split(r"(?<=[.!?])\s", first.strip())[0]   # the hook, not "Here's why." after it
    words = re.findall(r"[a-z0-9']+", first.lower())[:10]
    return {"#" if w.isdigit() else w for w in words if w not in _STOP}


def _repeats_opening(text: str, openings: list[str]) -> str:
    """The earlier opening this one copies, or "". Two openings match when most of
    their words are shared — "My last 7 cold pitches completely bombed" against
    "My first 7 LinkedIn posts completely bombed"."""
    mine = _opening_words(text)
    if len(mine) < 3:
        return ""
    for o in openings:
        theirs = _opening_words(o)
        if len(theirs) >= 3 and len(mine & theirs) / len(mine | theirs) >= 0.5:
            return o
    return ""


def _openings_to_avoid(company: dict, n: int = 10) -> list[str]:
    """First lines of the author's example posts and of this profile's recent posts."""
    out = []
    for post in (company.get("linkedin_top_posts") or [])[:5]:
        line = next((ln.strip() for ln in str(post).splitlines() if ln.strip()), "")
        if line:
            out.append(line[:160])
    try:
        for e in get_post_log([company.get("id", "")])[:n]:
            line = next((ln.strip() for ln in (e.get("post_text") or "").splitlines() if ln.strip()), "")
            if line:
                out.append(line[:160])
    except Exception:
        logger.exception("[Openings] could not read recent posts")
    seen, uniq = set(), []
    for line in out:
        if line.lower() not in seen:
            seen.add(line.lower()); uniq.append(line)
    return uniq


def profile_topics(company: dict) -> list[str]:
    """The topics a profile posts about, from its one "What are the posts about?" line, split on
    commas, semicolons or new lines. At most 5: more dilutes what LinkedIn reads as their
    expertise (360Brew favours a few related topics, posted consistently)."""
    raw = str(company.get("industry") or "")
    out, seen = [], set()
    for part in re.split(r"[,;\n|]+", raw):
        t = part.strip(" .-–")
        if len(t) >= 3 and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:5] or ([raw.strip()] if raw.strip() else [])


def news_search_args(company: dict) -> dict:
    """How to search the news for this profile: a person's region (from their time zone) and a
    business context, so an Indian coach's "top team effectiveness" finds workplace stories."""
    from carousel import _region_for
    personal = company.get("profile_type") == "personal"
    return {"region": _region_for(company), "context": "business workplace" if personal else ""}


def _topic_for_today(company: dict) -> str:
    """With several topics (an author, consultant and coach), the daily post takes them in turns:
    today's is the one used longest ago, a never-used one first. "" with a single topic."""
    topics = profile_topics(company)
    if len(topics) < 2:
        return ""
    last_used = {}   # topic -> how many posts ago (0 = the latest)
    try:
        for i, e in enumerate(get_post_log([company.get("id", "")])[:60]):
            t = (e.get("topic") or "").lower()
            if t and t not in last_used and e.get("status") not in ("failed", "skipped"):
                last_used[t] = i
    except Exception:
        logger.exception("[Topics] could not read recent posts")
    return max(topics, key=lambda t: (last_used.get(t.lower(), 10 ** 6), -topics.index(t)))


def _avoid_block(openings: list[str]) -> str:
    if not openings:
        return ""
    return ("\nRECENT POSTS (their first lines) — this post must open differently from every one of these, "
            "in wording AND in shape (no new number in the same template), and take a different angle "
            "on its subject, not the same point again:\n"
            + "\n".join(f"- {o}" for o in openings))


def _runs_today(company_id: str) -> int:
    """How many posts this company already made today (fed into the
    post-type rotation hash)."""
    from datetime import date
    today_str = date.today().isoformat()
    log = get_post_log([company_id])
    return sum(
        1 for e in log
        if e.get("timestamp", "").startswith(today_str)
        and e.get("status") in ("posted", "dry_run_fired", "pending_approval")
    )


# Self-promo guardrail: at most this many Product Spotlight posts per company
# per rolling week. Every other post is a value post about the profile's niche,
# not a sales pitch.
SPOTLIGHT_WEEKLY_CAP = 2


def _spotlights_last7(company_id: str) -> int:
    cutoff = (datetime.now() - timedelta(days=7)).isoformat()
    label = POST_TYPE_LABELS.get("product_spotlight", "Product Spotlight")
    return db.post_log.count_documents({
        "company_id": company_id,
        "post_type": label,
        "timestamp": {"$gte": cutoff},
        "status": {"$in": ["posted", "dry_run_fired", "pending_approval"]},
    })


def _get_post_type(company: dict, runs_today: int | None = None) -> str:
    """
    Returns a post type that varies by weekday AND by how many times this company
    has run today, so repeated Run Now clicks always produce a different type.
    """
    from datetime import date
    import hashlib

    is_personal = company.get("profile_type") == "personal"
    rotation = PERSONAL_ROTATION if is_personal else COMPANY_ROTATION

    today_str = date.today().isoformat()
    company_id = company.get("id", "default")
    if runs_today is None:
        runs_today = _runs_today(company_id)

    # Use a hash of (company_id + date + runs_today) to pick from rotation
    # This gives deterministic but varied results across runs
    seed = hashlib.md5(f"{company_id}{today_str}{runs_today}".encode()).hexdigest()
    idx = int(seed[:4], 16) % len(rotation)
    key = rotation[idx]

    # Weekly spotlight cap: advance deterministically to the next non-spotlight
    # slot. (get_week_plan doesn't model the cap — a capped day shows Spotlight
    # in the forecast but posts the substitute; acceptable drift.)
    if key == "product_spotlight" and _spotlights_last7(company_id) >= SPOTLIGHT_WEEKLY_CAP:
        for step in range(1, len(rotation)):
            alt = rotation[(idx + step) % len(rotation)]
            if alt != "product_spotlight":
                return alt
    return key


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
    company_id = company.get("id", "default")
    # The same forecast the calendar shows: today's post if it hasn't run yet,
    # otherwise tomorrow's. (It used to predict a *second* run today.)
    plan = get_week_plan(company, 2)
    next_type_label = plan[1] if _runs_today(company_id) else plan[0]

    log = get_post_log([company_id])
    recent = []
    for entry in log:   # newest first
        if entry.get("company_id") == company_id and entry.get("status") in ("posted", "dry_run_fired", "pending_approval"):
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


# Claim-shaped post types automatically ship with a tweet card: the hook line rendered
# as the social-screenshot image (deterministic typography — no AI-image lottery, safe
# to run unattended). Scene illustrations remain manual-only by design.
TWEET_CARD_TYPES = ("hot_take", "prediction", "stat_reaction")


def _tweet_card_for(post_text: str, company: dict):
    """Render the post's hook line as a tweet-card PNG. Fail-open: any problem -> None
    and the post ships as plain text."""
    try:
        from carousel import _slide_tweet_card, _get_palette, IMG_POST_W, _SCALE
        from PIL import Image
        import io as _io
        hook = next((l.strip() for l in post_text.splitlines() if l.strip()), "")
        if not (10 <= len(hook) <= 180):
            return None
        from carousel import _card_author
        handle = (company.get("designation") or "").strip()   # real title, never an invented @handle
        # The account it posts as: its LinkedIn name and photo, else the profile name
        author = li.author_for_profile(company, company.get("user_id", ""))
        name, avatar = _card_author(company.get("name", "") or "Voyce", author)
        img = _slide_tweet_card(hook, name, handle, _get_palette(company),
                                rw=IMG_POST_W * _SCALE, avatar=avatar)
        buf = _io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
    except Exception as e:
        logger.warning(f"[Autonomous] tweet card render failed, posting plain text: {e}")
        return None


def generate_autonomous_post(company: dict, news_context: str, post_type: str,
                             gate_context: str | None = None, report: dict | None = None,
                             use_note: bool = True) -> str:
    """Write one post. gate_context is what the fact check may treat as true
    (defaults to news_context); pass a dict as `report` to learn whether the
    returned post actually passed — unattended runs must not publish one that didn't."""
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
    elif not _knows_their_past(company):
        # Lesson Learned and Personal Story ask for a memory. With nothing about the
        # author's past, the writer made one up, and the fact check then failed it.
        prompt += (
            "\n\nNOTHING IS KNOWN ABOUT THIS AUTHOR'S PAST (no past posts, site or facts). "
            "Do not write a memory: no \"I spent...\", \"for years I...\", \"we missed...\", no "
            "client, team or career story, no result from their own work, no claim about what "
            "they see at work (\"as a CFO I see this every day\"). Write it as a "
            f"present-tense take instead: the lesson people in {industry} keep learning the hard "
            "way, anchored in a real case from the news. \"I think\", \"here's what I'd do\" and "
            "advice to the reader are fine.")
    note = notes_mod.pick(company) if use_note else None
    if note:
        prompt += notes_mod.prompt_block(note)
    prompt += _hook_guidance(company.get("allowed_hooks"))
    openings = _openings_to_avoid(company)
    prompt += _avoid_block(openings)

    draft = _format_linkedin_post(
        llm_generate(prompt, max_tokens=1024, temperature=0.85)
    )
    copied = _repeats_opening(draft, openings)
    if copied:
        # The prompt alone didn't stop it: say exactly which opening it reused.
        logger.info(f"[Openings] draft reused an opening ({copied[:60]!r}); rewriting once")
        draft = _format_linkedin_post(llm_generate(
            prompt + f"\n\nYOUR LAST DRAFT OPENED LIKE THIS EARLIER POST: \"{copied}\". "
                     "Write the post again with a completely different first line and angle.",
            max_tokens=1024, temperature=0.9))
    post = _revise_post(draft, industry)

    # VALUE GATE: unattended posts must earn their place in someone's feed. One brutal
    # reader-judge scores the post; a fail triggers exactly one feedback-fed rewrite.
    facts = news_context if gate_context is None else gate_context
    author = _author_record(company)
    verdict = _value_gate(post, industry, facts, author_context=author)
    chosen, chosen_v = post, verdict
    if not verdict.get("passes", True):
        retry = prompt + (
            f"\n\nYOUR PREVIOUS DRAFT FAILED REVIEW.\nWeakness: {verdict.get('reason', '')}\n"
            "Rewrite the post fixing exactly that weakness. Keep every quality rule. If the "
            "weakness is an invented experience, drop the memory and keep the point: make it "
            "an observation, an opinion or advice instead."
        )
        post2 = _revise_post(_format_linkedin_post(
            llm_generate(retry, max_tokens=1024, temperature=0.9)), industry)
        v2 = _value_gate(post2, industry, facts, author_context=author)
        if v2.get("score", 0) >= verdict.get("score", 0):
            chosen, chosen_v = post2, v2
    if report is not None:
        report.update({"passed": bool(chosen_v.get("passes", True)),
                       "score": chosen_v.get("score"), "reason": chosen_v.get("reason", ""),
                       "note_id": note["id"] if note else ""})
    if note and company.get("id"):
        notes_mod.mark_used(company["id"], note["id"])
    return drop_author_furniture(chosen, company)


def _knows_their_past(company: dict) -> bool:
    """True when the profile holds anything about the author's real past."""
    return bool(company.get("linkedin_top_posts") or company.get("author_notes")
                or (company.get("knowledge") or "").strip() or (company.get("profile_facts") or "").strip()
                or (company.get("website_content") and company.get("website_type") != "external")
                or (company.get("analysis") or {}).get("description"))


def _author_record(company: dict) -> str:
    """What is actually known about the author's past, for the invented-history check:
    their own posts, own site, background and facts. No writing instructions: given the
    full writer brief, the judge enforced those too and failed real memories as
    "retelling an example's story"."""
    role = (company.get("designation") or "").strip()
    parts = [f"Author: {company.get('name', '')}" + (f", {role}" if role else "")]
    analysis = company.get("analysis") or {}
    if analysis.get("description"):
        parts.append(f"Background: {analysis['description']}")
    if analysis.get("products_services"):
        parts.append(f"Products/services: {', '.join(analysis['products_services'])}")
    if company.get("website_content") and company.get("website_type") != "external":
        parts.append("Their own website:\n" + company["website_content"][:1200])
    for i, post in enumerate((company.get("linkedin_top_posts") or [])[:5], 1):
        parts.append(f"Their past post {i}:\n{str(post)[:600]}")
    if (company.get("knowledge") or "").strip():
        parts.append("Facts they gave:\n" + company["knowledge"].strip()[:2000])
    if (company.get("profile_facts") or "").strip():
        parts.append("Their LinkedIn profile (career):\n" + company["profile_facts"].strip()[:8000])
    told = notes_mod.record_block(company)
    if told:
        parts.append(told)
    return "\n".join(parts)


def _value_gate(post: str, industry: str, news_context: str = "",
                author_context: str | None = None) -> dict:
    """Score a finished post as a busy reader would AND catch unverifiable stats before they
    reach the author's real profile. With author_context (what the writer was told about the
    author), it also fails made-up personal history: a first post for a CFO with no past
    posts opened "I treated finance like a brake pedal for years", a memory she never had.
    Fail-open: gate errors never block posting."""
    from llm import generate_json
    history_axis, history_rule = "", ""
    if author_context is not None:
        history_axis = f"""
AUTHOR MATERIAL (the ONLY record of the author's real past: their own posts, site, facts):
{author_context.strip() or "(none: nothing is known about this author's past)"}
"""
        history_rule = """
3. INVENTED HISTORY — does the post claim something happened to the author in the past
   ("I/we did, built, hired, lost, learned...", "for years I...", "last quarter we...", a
   client or team story, a result or number from their own work) that the AUTHOR MATERIAL
   does not support? If yes, that is an automatic FAIL — it is a made-up memory published
   under their name. So is a claim about what they see or do at work ("As a CFO, I see
   this every day", "my clients keep asking") that the material doesn't support.
   Present-tense opinions, beliefs, advice and hypotheticals ("I think",
   "here's what I'd do", "if you run finance...") are fine, and so is retelling
   anything that IS in the author material."""
    prompt = f"""You are a brutal, busy LinkedIn reader in the {industry} space, scrolling fast,
who also fact-checks reflexively.

POST:
{post}

SOURCE NEWS the post was allowed to draw from (the ONLY place checkable facts may come from):
{news_context or "(none provided)"}
{history_axis}
Judge on {"THREE" if history_rule else "TWO"} axes:
1. VALUE — does it teach, reveal, or argue something specific a busy pro is glad they read?
   Generic filler, vague motivation, or obvious advice = fail.
2. FACTUAL SAFETY — does it state any specific checkable fact (a named company's financials,
   a precise percentage like "92%", a funding number, a product version, a study) that is NOT
   present in the source news above? If yes, that is an automatic FAIL — those are fabrications
   on the author's real profile. A fact from the news stated MORE strongly than the news
   states it (dropping "up to", "expected" or "will", turning a plan into a done deal) is a
   fabrication too.{history_rule}

Return ONLY JSON: {{"score": 1-10, "passes": true or false (passes = score >= 7 AND no
unverifiable stat{" AND no invented history" if history_rule else ""}), "reason": "one line naming the biggest problem (quote the fabricated stat or memory if any)"}}"""
    try:
        v = generate_json(prompt, max_tokens=300, temperature=0.2)
        return {"score": int(v.get("score", 7)), "passes": bool(v.get("passes", True)),
                "reason": str(v.get("reason", ""))[:200]}
    except Exception:
        return {"score": 7, "passes": True, "reason": ""}


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
        # Skip hashtag and list lines, and anything already laid out in lines (an
        # intro with a list under it): splitting those cut "2." off its item.
        if para.startswith("#") or re.match(r"^\s*(\d+[\.\)]|[-•→])", para) or "\n" in para:
            result.append(para)
            continue
        sentences = []
        for s in re.split(r'(?<=[.!?])\s+', para):
            s = s.strip()
            if not s:
                continue
            if sentences and re.fullmatch(r"\d+[.)]", sentences[-1]):
                sentences[-1] += " " + s    # "2." is a list number, not a sentence
            else:
                sentences.append(s)
        if len(sentences) <= 3:
            result.append(para)
        else:
            # Wall of text — split into blocks of 2-3 sentences
            for i in range(0, len(sentences), 3):
                result.append(" ".join(sentences[i:i+3]))

    return "\n\n".join(result)


# How many daily posts a week are carousels. 1-3 land on fixed weekdays in the
# profile's own time zone (spread out, never back to back); "alt" alternates with
# the profile's last post. Profiles from before the choice existed: switch on = "alt".
CAROUSEL_CHOICES = (0, 1, 2, 3, "alt")
CAROUSEL_WEEKDAYS = {1: {2}, 2: {1, 3}, 3: {0, 2, 4}}   # Mon=0: Wed · Tue+Thu · Mon+Wed+Fri
_DAILY_STATUSES = ("posted", "pending_approval", "expired", "discarded", "superseded", "dry_run_fired")


def carousel_mode(company: dict):
    v = company.get("carousels_per_week")
    if v in CAROUSEL_CHOICES:
        return v
    return "alt" if company.get("carousel_enabled") else 0


def _last_daily(company: dict) -> dict | None:
    try:
        return next((e for e in get_post_log([company.get("id", "")]) if e.get("status") in _DAILY_STATUSES), None)
    except Exception:
        logger.exception("[Carousel] could not read the last post")
        return None


def _should_post_carousel(company: dict, day: date | None = None) -> bool:
    mode = carousel_mode(company)
    if not mode:
        return False
    if mode == "alt":
        last = _last_daily(company)
        return not last or last.get("post_format") != "carousel"
    return (day or _local_today(company)).weekday() in CAROUSEL_WEEKDAYS[mode]


def get_week_forecast(company: dict, days: int = 7) -> list[dict]:
    """What each daily run will write, today first: the profile-local date, the post
    type and the format. Same rules as run_for_company, so the app shows the plan
    instead of guessing it."""
    from zoneinfo import ZoneInfo
    from company import valid_tz, DEFAULT_TZ
    labels = get_week_plan(company, days)
    key_of = {v: k for k, v in POST_TYPE_LABELS.items()}
    start = _local_today(company)
    sched = company.get("scheduled_types") or {}
    mode = carousel_mode(company)
    last = _last_daily(company)
    last_car = bool(last) and last.get("post_format") == "carousel"
    ran_today = False
    if last:
        try:
            ts = datetime.fromisoformat(last["timestamp"]).astimezone(ZoneInfo(valid_tz(company.get("timezone", "")) or DEFAULT_TZ))
            ran_today = ts.date() == start
        except Exception:
            pass
    out = []
    for i in range(days):
        d = start + timedelta(days=i)
        label = labels[i] if i < len(labels) else ""
        planned = sched.get(d.isoformat()) or ""
        if isinstance(planned, dict):
            planned = planned.get("type", "")
        if planned == "__video__":
            fmt = "video"
        elif planned == "__carousel__":
            fmt = "carousel"
        elif planned in POST_TYPE_LABELS:
            label = POST_TYPE_LABELS[planned]   # an explicit type is never a carousel
            fmt = "card" if planned in TWEET_CARD_TYPES else "text"
        else:
            if mode == "alt":
                car = (not last_car) if not (i == 0 and ran_today) else last_car
            else:
                car = bool(mode) and d.weekday() in CAROUSEL_WEEKDAYS[mode]
            fmt = "carousel" if car else ("card" if key_of.get(label) in TWEET_CARD_TYPES else "text")
        if not (i == 0 and ran_today):
            last_car = fmt == "carousel"
        out.append({"date": d.isoformat(), "post_type": label, "format": fmt, "planned": planned})
    return out


def _video_gen_prompt(subject: dict, post_text: str) -> str:
    """A copy-paste prompt for the user's own AI video tool (Sora/Veo/Runway —
    or Claude for a storyboard). Voyce never generates the video itself; the
    user takes this prompt to whatever they subscribe to and posts natively.
    The profile's stored palette (if any) rides along so DIY assets stay
    on-brand. Fail-open: a template beats a missing prompt."""
    palette = (subject.get("theme_spec") or {}).get("palette") or {}
    brand = ""
    if palette:
        brand = (f"Brand colors: background {palette.get('bg', '')}, "
                 f"accent {palette.get('accent', '')}, text {palette.get('title', '')}. ")
    elif subject.get("brand_color"):
        brand = f"Brand accent color: {subject['brand_color']}. "
    who = subject.get("product_name") or subject.get("name", "")

    fallback = (
        f"Create a 25-second vertical (9:16) social video for {who} "
        f"({subject.get('industry', '')}). {brand}"
        "Open with a bold on-screen text hook taken from the first line of the post "
        "below, then 3 quick scenes that visualize its main point, and end with a "
        "clean logo/name card. Modern, minimal, high-contrast; readable captions; "
        "no stock-footage feel.\n\nThe post this video accompanies:\n" + post_text[:900]
    )
    try:
        prompt = llm_generate(
            f"Brand/product: {who} — {subject.get('industry', '')}\n{brand}\n"
            f"LinkedIn post the video will accompany:\n{post_text[:1200]}",
            system=(
                "Write ONE copy-paste prompt for an AI video generator (Sora, Veo, "
                "Runway) that produces a 20-30s vertical social video to accompany "
                "this LinkedIn post. The prompt must: describe 3-4 concrete scenes "
                "tied to the post's actual argument (not generic b-roll), specify "
                "on-screen text for the hook, name the brand colors if given, and "
                "state format (9:16, readable captions, minimal/premium look). "
                "Return ONLY the prompt text, no preamble."
            ),
            max_tokens=600,
            temperature=0.7,
        )
        return prompt.strip() if prompt and prompt.strip() else fallback
    except Exception:
        return fallback


def _local_today(company: dict) -> date:
    """Today in the profile's own time zone — the calendar plan is keyed by the
    user's local date, which differs from the server's for much of the day."""
    from zoneinfo import ZoneInfo
    from company import valid_tz, DEFAULT_TZ
    try:
        return datetime.now(ZoneInfo(valid_tz(company.get("timezone", "")) or DEFAULT_TZ)).date()
    except Exception:
        return date.today()


CLAIM_STALE_MINUTES = 20   # a claimed run older than this that never finished died mid-way


def claim_scheduled_run(company: dict) -> str | None:
    """Claim today's scheduled post for this profile; returns the claim key, or
    None if it's already taken.

    During a deploy the old and new servers both run the scheduler for about a
    minute, and the startup catch-up can fire next to the cron, so the same
    profile's daily post could be written and published twice. The first run to
    claim the profile's local day wins. A claim that never finished (the server
    was killed mid-run) is taken over after CLAIM_STALE_MINUTES, so a deploy can't
    lose the day's post either. Manual "Post now" runs never claim."""
    from pymongo.errors import DuplicateKeyError
    key = f"{company['id']}:{_local_today(company).isoformat()}"
    now = datetime.now()
    try:
        db.run_claims.insert_one({"_id": key, "started_at": now.isoformat(), "finished": False,
                                  "created": datetime.utcnow()})
        return key
    except DuplicateKeyError:
        stale = (now - timedelta(minutes=CLAIM_STALE_MINUTES)).isoformat()
        r = db.run_claims.update_one(
            {"_id": key, "finished": False, "started_at": {"$lt": stale}},
            {"$set": {"started_at": now.isoformat(), "taken_over": True}})
        return key if r.modified_count == 1 else None
    except Exception:
        logger.exception(f"[Claim] could not claim {key}; running anyway rather than skip the day")
        return key


def finish_scheduled_run(key: str):
    try:
        db.run_claims.update_one({"_id": key}, {"$set": {"finished": True,
                                                         "finished_at": datetime.now().isoformat()}})
    except Exception:
        logger.exception(f"[Claim] could not mark {key} finished")


def _strings_in(obj) -> list[str]:
    """Every piece of text in a generated carousel, whatever its schema."""
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [s for v in obj.values() for s in _strings_in(v)]
    if isinstance(obj, (list, tuple)):
        return [s for v in obj for s in _strings_in(v)]
    return []


def _held_reason(verdict: dict) -> str:
    why = (verdict.get("reason") or "").strip()
    if len(why) > 160:
        why = why[:160].rstrip() + "…"
    return "it didn't pass Voyce's fact and quality check" + (f" ({why})" if why else "")


def run_for_company(company: dict, allow_free_manual: bool = False,
                    post_type_override: str = "", respect_approval: bool = True,
                    notify_user: bool = True) -> dict:
    """notify_user=False for a manual "Post now": the result is already on screen."""
    company_id = company["id"]
    is_personal = company.get("profile_type") == "personal"
    valid_types = set((PERSONAL_ROTATION if is_personal else COMPANY_ROTATION).values())
    runs_today = _runs_today(company_id)
    local_today = _local_today(company)

    # A per-day plan set from the calendar takes precedence over the rotation (unless a
    # manual "Post now" passed an explicit override, which wins over everything).
    if not post_type_override:
        plan = (company.get("scheduled_types") or {}).get(local_today.isoformat())
        if isinstance(plan, dict):   # tolerate legacy product-pinned rows
            plan = plan.get("type", "")
        if plan in ("__carousel__", "__video__"):
            post_type_override = ""  # keep rotation type, but force the format below
        elif plan in valid_types:
            post_type_override = plan
    else:
        plan = None
    do_diy_video = False
    if post_type_override and post_type_override in valid_types:
        post_type = post_type_override
        do_carousel = False  # an explicit type choice is a text/tweet intent, never a carousel
    elif plan == "__carousel__":
        post_type = _get_post_type(company, runs_today)
        do_carousel = True
    elif plan == "__video__":
        # DIY video day: Voyce writes the caption + a generation prompt; the user
        # generates the video with their own AI tool and posts natively on
        # LinkedIn. Nothing is auto-published — the item always waits in the queue.
        post_type = _get_post_type(company, runs_today)
        do_carousel = False
        do_diy_video = True
    else:
        post_type  = _get_post_type(company, runs_today)
        do_carousel = _should_post_carousel(company, local_today)

    # The subject for this post is the profile itself (its voice, niche, knowledge). With several
    # topics, today's is taken in turn: the news search, the post and its checks work on that one,
    # and the brief still lists them all. It was one string searched as a whole, so a profile with
    # three sides got posts about whichever the news favoured that week.
    subject = company
    today_topic = _topic_for_today(company)
    if today_topic:
        subject = {**company, "industry": today_topic, "all_topics": company.get("industry", "")}

    log_entry = {
        "company_id":   company_id,
        "company_name": company["name"],
        "product_name": subject.get("product_name", ""),
        "post_type":    POST_TYPE_LABELS.get(post_type, post_type),
        "post_format":  "diy_video" if do_diy_video else ("carousel" if do_carousel else "text"),
        "timestamp":    datetime.now().isoformat(),
        "status":       "failed",
        "post_text":    "",
        "error":        "",
    }
    if today_topic:
        log_entry["topic"] = today_topic   # so the next run takes the next topic

    try:
        user_id = company.get("user_id", "")
        gen_info = auth_module.get_gen_info(user_id)
        if gen_info["limit"] != -1 and gen_info["used"] >= gen_info["limit"]:
            pass_used_up = gen_info.get("plan") == "pro"   # an India pass, not a lapsed one
            log_entry["status"] = "skipped"
            log_entry["error"] = ("This pass's generations are used up" if pass_used_up
                                  else "Free generation limit reached")
            _append_log(log_entry)
            if notify_user:
                notify.autopilot_paused(company, reason="pass_limit" if pass_used_up else "")
            return log_entry

        # A profile can publish to its own LinkedIn account (see linkedin.connection_for).
        conn = li.connection_for(company, user_id)
        if not li.is_connected(conn):
            raise ValueError("LinkedIn not connected for this profile")

        NEWS_HEAVY  = ("trend_commentary", "industry_stat", "trend_reaction", "stat_reaction")
        num_results = 6 if post_type in NEWS_HEAVY else 3
        # Pass post_type so search uses targeted queries for this specific post angle.
        # The profile carries the niche and any extra search angles.
        news_results = search_industry_news(
            subject["industry"], subject["name"], num_results, post_type=post_type,
            extra_angles=subject.get("search_angles") or [], **news_search_args(company),
        )
        news_context = format_news_context(news_results)
        # What the post was built from, kept with it so the user can check it. The
        # fact check only accepts real articles as evidence — Tavily's own
        # AI-written summary (the entry with no URL) is useful context for writing,
        # but treating it as proof let its mistakes pass as "verified".
        articles = [r for r in news_results if r.get("url")]
        sources = [{"title": r.get("title", ""), "url": r["url"]} for r in articles][:3]
        gate_context = format_news_context(articles)
        log_entry["sources"] = sources
        report: dict = {}

        import base64 as _b64
        if do_diy_video:
            post_text = generate_autonomous_post(subject, news_context, post_type,
                                                 gate_context=gate_context, report=report)
            payload = {"format": "diy_video", "post_text": post_text, "publish_text": post_text,
                       "asset_b64": "", "alt_text": "", "title": company["name"],
                       "gen_prompt": _video_gen_prompt(subject, post_text)}
        elif do_carousel:
            from carousel import generate_carousel_content, render_carousel_pdf
            content   = generate_carousel_content(subject, news_context, post_type)
            # Carousels used to skip the fact check entirely, though their prompt
            # demands named companies and numbers. Check every slide's text.
            v = _value_gate("\n".join(_strings_in(content)), subject["industry"], gate_context,
                            author_context=_author_record(subject))
            report.update({"passed": bool(v.get("passes", True)), "reason": v.get("reason", "")})
            previews: list = []
            try:   # their name and photo on the cover and the last slide; a deck without them still posts
                author = li.author_for_profile(company, company.get("user_id", "")) or None
            except Exception:
                logger.exception("[Carousel] author lookup failed")
                author = None
            pdf_bytes = render_carousel_pdf(content, subject, previews=previews, author=author)
            post_text = content.get("post_text", "")
            payload = {"format": "carousel", "post_text": post_text, "publish_text": post_text,
                       "asset_b64": _b64.b64encode(pdf_bytes).decode(), "alt_text": "",
                       "title": company["name"], "slide_previews": previews}
        else:
            post_text = generate_autonomous_post(subject, news_context, post_type,
                                                 gate_context=gate_context, report=report)
            card_png = _tweet_card_for(post_text, subject) if post_type in TWEET_CARD_TYPES else None
            if card_png:
                # The card already displays the hook line — don't repeat it as the text opener.
                lines = post_text.splitlines()
                first_idx = next((i for i, l in enumerate(lines) if l.strip()), 0)
                rest = "\n".join(lines[first_idx + 1:]).strip()
                text_for_post = rest if len(rest.split()) >= 40 else post_text
                payload = {"format": "tweet_card", "post_text": post_text, "publish_text": text_for_post,
                           "asset_b64": _b64.b64encode(card_png).decode(),
                           "alt_text": (lines[first_idx].strip()[:120] if lines else "post card"),
                           "title": company["name"]}
            else:
                payload = {"format": "text", "post_text": post_text, "publish_text": post_text,
                           "asset_b64": "", "alt_text": "", "title": company["name"]}

        # Carry the subject label so pending items + approval-time log entries stay attributable.
        payload["product_name"] = subject.get("product_name", "")
        payload["sources"] = sources
        # A post that failed the fact check never goes out unseen, even with
        # approval mode off and even on a manual run: it waits for a human.
        held_reason = "" if report.get("passed", True) else _held_reason(report)
        payload["held_reason"] = held_reason

        log_entry["post_text"] = payload["post_text"]
        # DIY video items can never auto-publish (the asset doesn't exist until
        # the user generates it) — they always wait in the queue, approval mode
        # or not, manual run or not.
        if do_diy_video or held_reason or (respect_approval and company.get("approval_mode")):
            pending_id = _save_pending(company, post_type, payload)
            log_entry["status"] = "pending_approval"
            log_entry["pending_id"] = pending_id
            if held_reason:
                log_entry["held_reason"] = held_reason
            auth_module.increment_gens(user_id)
            logger.info(f"[Autonomous] {POST_TYPE_LABELS.get(post_type, post_type)} held for approval "
                        f"({company['name']}){' — failed gate' if held_reason else ''}")
            if notify_user:
                notify.post_ready(company, pending_id, POST_TYPE_LABELS.get(post_type, post_type),
                                  payload["post_text"], sources, held_reason)
            analytics.capture(user_id, "post_queued_for_approval", {"post_type": post_type, "format": payload.get("format", "text"),
                                                                  "emailed": bool(notify_user), "held": bool(held_reason)})
        else:
            result = _publish_payload(conn, payload)
            log_entry["post_urn"] = result.get("id", "")
            log_entry["status"] = "posted"
            analytics.capture(user_id, "post_published", {"via": "autopilot", "post_type": post_type,
                                                       "format": payload.get("format", "text")})
            if result.get("fallback_from"):
                log_entry.update({"post_format": "text", "fallback_from": result["fallback_from"],
                                  "post_text": result["fallback_text"],
                                  "error": f"Posted as text: the {result['fallback_from']} failed "
                                           f"({result.get('media_error', '')})"})
            auth_module.increment_gens(user_id)
            logger.info(f"[Autonomous] {POST_TYPE_LABELS.get(post_type, post_type)} posted for {company['name']}")

    except Exception as e:
        log_entry["error"] = str(e)
        logger.error(f"[Autonomous] Failed for {company['name']}: {e}")
        if notify_user:
            notify.post_failed(company, str(e), local_today.isoformat())

    _append_log(log_entry)
    return log_entry


# ── Approval queue ────────────────────────────────────────────────────────────
# "Ask me before it posts": scheduled runs generate the full post (news, text,
# card/carousel asset) and hold it here instead of publishing. The user approves
# or discards from the dashboard. News posts go stale, so unapproved items
# expire after PENDING_TTL_HOURS rather than publishing outdated takes.

PENDING_TTL_HOURS = 48


# Lines of a carousel caption that point at the slides make no sense alone.
_SLIDE_REF = re.compile(r"\b(swipe|slides?|carousel|pdf|document)\b|[👇👉➡⬇→]", re.I)


def _text_fallback(payload: dict) -> str:
    """What to post as plain text if the image or PDF can't be published, or ""
    if there isn't a real post left once the visual is gone."""
    text = (payload.get("post_text") or payload.get("publish_text") or "").strip()
    if payload.get("format") == "carousel":
        text = "\n".join(l for l in text.splitlines() if not _SLIDE_REF.search(l))
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text if len(text.split()) >= 25 else ""


def _publish_payload(user_id: str, payload: dict) -> dict:
    """Publish a generated payload to LinkedIn — the single publish path shared by
    immediate posting and later approval.

    Image and carousel posts go through LinkedIn's versioned /rest APIs, text
    through the older ugcPosts endpoint. If the media path fails, the day's post
    still goes out as text rather than not at all; the result says so."""
    import base64 as _b64
    fmt = payload.get("format", "text")
    if fmt not in ("carousel", "tweet_card"):
        return li.post_to_linkedin(user_id, payload.get("publish_text", ""))
    try:
        if fmt == "carousel":
            pdf = _b64.b64decode(payload.get("asset_b64", ""))
            return li.upload_and_post_carousel(user_id, pdf, payload.get("publish_text", ""),
                                               title=payload.get("title", ""))
        png = _b64.b64decode(payload.get("asset_b64", ""))
        return li.upload_and_post_image(user_id, png, payload.get("publish_text", ""),
                                        alt_text=payload.get("alt_text", "post card"))
    except Exception as media_err:
        text = _text_fallback(payload)
        if not text:
            raise
        logger.warning(f"[Publish] {fmt} failed ({media_err}); posting it as text instead")
        try:
            result = li.post_to_linkedin(user_id, text)
        except Exception as text_err:
            raise ValueError(f"{media_err} The text-only retry failed too: {text_err}") from text_err
        return {**result, "fallback_from": fmt, "fallback_text": text,
                "media_error": str(media_err)[:300]}


def _save_pending(company: dict, post_type: str, payload: dict) -> str:
    """Store a held post. One pending item per profile: a fresh generation supersedes
    any older unapproved one (yesterday's news post shouldn't linger as an option)."""
    import uuid
    import secrets
    import base64 as _b64
    old = [p["id"] for p in db.pending_posts.find({"company_id": company["id"], "status": "pending"}, {"id": 1})]
    if old:
        db.pending_posts.update_many({"id": {"$in": old}}, {"$set": {"status": "superseded"}})
        db.post_log.update_many({"pending_id": {"$in": old}, "status": "pending_approval"},
                                {"$set": {"status": "superseded"}})
    if payload.get("format") == "tweet_card" and payload.get("asset_b64") and not payload.get("slide_previews"):
        try:
            from carousel import slide_previews
            from PIL import Image
            import io as _io
            payload = {**payload, "slide_previews": slide_previews(
                [Image.open(_io.BytesIO(_b64.b64decode(payload["asset_b64"])))])}
        except Exception:
            logger.exception("[Pending] card preview failed")
    pend = {
        "id":           uuid.uuid4().hex[:12],
        # The link in the approval email: whoever holds it can approve or skip this
        # one post, until it's handled or expires. Opening it only shows the post.
        "approve_token": secrets.token_urlsafe(24),
        "user_id":      company.get("user_id", ""),
        "company_id":   company["id"],
        "company_name": company.get("name", ""),
        "post_type":    POST_TYPE_LABELS.get(post_type, post_type),
        "status":       "pending",
        "created_at":   datetime.now().isoformat(),
        **payload,
    }
    db.pending_posts.insert_one({**pend})
    return pend["id"]


def expires_at(pend: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(pend["created_at"]) + timedelta(hours=PENDING_TTL_HOURS)
    except Exception:
        return None


def is_expired(pend: dict) -> bool:
    exp = expires_at(pend)
    return bool(exp and datetime.now() >= exp)


def list_pending_posts(user_id: str) -> list:
    """Pending items for the dashboard queue (assets excluded — they're big).
    Lazily expires stale items first."""
    cutoff = (datetime.now() - timedelta(hours=PENDING_TTL_HOURS)).isoformat()
    stale = [p["id"] for p in db.pending_posts.find(
        {"user_id": user_id, "status": "pending", "created_at": {"$lt": cutoff}}, {"id": 1})]
    if stale:
        db.pending_posts.update_many({"id": {"$in": stale}}, {"$set": {"status": "expired"}})
        db.post_log.update_many({"pending_id": {"$in": stale}, "status": "pending_approval"},
                                {"$set": {"status": "expired"}})
    items = list(db.pending_posts.find(
        {"user_id": user_id, "status": "pending"},
        {"_id": 0, "asset_b64": 0, "approve_token": 0}).sort("created_at", -1))
    for p in items:
        exp = expires_at(p)
        # With their offset, so the browser reads them right (server time is UTC
        # in production; a bare "09:00" was read as 9am local — 5½ hours off in India).
        p["expires_at"] = exp.astimezone().isoformat() if exp else None
        try:
            p["created_at"] = datetime.fromisoformat(p["created_at"]).astimezone().isoformat()
        except Exception:
            pass
    return items


def approve_pending_post(pending_id: str, user_id: str, edited_text: str = "") -> dict:
    """Publish a held post through the normal publish path. On publish failure the
    item stays pending so the user can retry.

    edited_text replaces the caption when the user tweaked it in the queue — the
    generated asset (tweet card, carousel) is kept as-is, since it carries its own
    line rather than a copy of the caption."""
    pend = db.pending_posts.find_one({"id": pending_id, "user_id": user_id}, {"_id": 0})
    if not pend or pend.get("status") != "pending":
        return {"error": "not_found"}
    if is_expired(pend):
        # Stale news must not ship — an email link opened days later included.
        db.pending_posts.update_one({"id": pending_id, "status": "pending"}, {"$set": {"status": "expired"}})
        db.post_log.update_many({"pending_id": pending_id, "status": "pending_approval"}, {"$set": {"status": "expired"}})
        return {"error": "expired"}
    # Claim it, so a second tap (or the email and the dashboard at once) can't publish twice.
    if not db.pending_posts.update_one({"id": pending_id, "status": "pending"},
                                       {"$set": {"status": "publishing"}}).modified_count:
        return {"error": "not_found"}
    edited_text = (edited_text or "").strip()
    if edited_text and edited_text != (pend.get("post_text") or "").strip():
        pend["publish_text"] = edited_text
        pend["post_text"] = edited_text          # the log should show what actually went out
        db.pending_posts.update_one({"id": pending_id},
                                    {"$set": {"post_text": edited_text, "edited": True}})
    if pend.get("format") == "diy_video":
        # "I posted it": the user generated the video and published natively on
        # LinkedIn themselves — record the day as covered, publish nothing.
        db.pending_posts.update_one(
            {"id": pending_id},
            {"$set": {"status": "approved", "approved_at": datetime.now().isoformat()}})
        _append_log({
            "company_id":   pend.get("company_id", ""),
            "company_name": pend.get("company_name", ""),
            "product_name": pend.get("product_name", ""),
            "post_type":    pend.get("post_type", ""),
            "post_format":  "diy_video",
            "timestamp":    datetime.now().isoformat(),
            "status":       "posted",
            "post_text":    pend.get("post_text", ""),
            "post_urn":     "",   # posted natively — no URN, so engagement refresh skips it
            "error":        "",
        })
        logger.info(f"[Approval] DIY video {pending_id} marked as posted manually")
        return {"status": "posted_manually"}
    # Publish through the profile's own LinkedIn if it has one, else the owner's.
    company = db.companies.find_one({"id": pend.get("company_id", "")}, {"_id": 0}) or {}
    conn = li.connection_for(company, user_id)
    try:
        result = _publish_payload(conn, pend)
    except Exception:
        # Publishing failed: back in the queue, so the user can retry.
        db.pending_posts.update_one({"id": pending_id, "status": "publishing"}, {"$set": {"status": "pending"}})
        raise
    db.pending_posts.update_one(
        {"id": pending_id},
        {"$set": {"status": "approved", "approved_at": datetime.now().isoformat()},
         "$unset": {"asset_b64": ""}})
    fell_back = result.get("fallback_from", "")
    published = {
        "post_format":  "carousel" if pend.get("format") == "carousel" and not fell_back else "text",
        "timestamp":    datetime.now().isoformat(),   # when it went out
        "status":       "posted",
        "post_text":    result.get("fallback_text") or pend.get("post_text", ""),
        "post_urn":     result.get("id", ""),
        "sources":      pend.get("sources", []),
        "fallback_from": fell_back,
        "error":        (f"Posted as text: the {fell_back} failed ({result.get('media_error', '')})"
                         if fell_back else ""),
    }
    # One row per post: the "Waiting for approval" row becomes the posted one.
    # Appending a second row left the first saying "waiting" forever.
    if not db.post_log.update_one({"pending_id": pending_id},
                                  {"$set": {**published, "approved_at": published["timestamp"]}}).matched_count:
        _append_log({
            "company_id":   pend.get("company_id", ""),
            "company_name": pend.get("company_name", ""),
            "product_name": pend.get("product_name", ""),
            "post_type":    pend.get("post_type", ""),
            **published,
        })
    logger.info(f"[Approval] Pending post {pending_id} approved and published"
                + (f" as text ({fell_back} failed)" if fell_back else ""))
    return {"status": "posted", "post_urn": result.get("id", ""),
            **({"posted_as": "text", "media_error": result.get("media_error", "")} if fell_back else {})}


def discard_pending_post(pending_id: str, user_id: str) -> dict:
    r = db.pending_posts.update_one(
        {"id": pending_id, "user_id": user_id, "status": "pending"},
        {"$set": {"status": "discarded"}, "$unset": {"asset_b64": ""}})
    if r.modified_count:
        db.post_log.update_many({"pending_id": pending_id, "status": "pending_approval"},
                                {"$set": {"status": "discarded"}})
    return {"ok": r.modified_count > 0}


def _append_log(entry: dict):
    db.post_log.insert_one({**entry})


def get_post_log(company_ids: list[str] | None = None) -> list:
    """Post log entries, newest first. Pass company_ids to scope the query —
    loading the whole collection degrades with global volume and leaks other
    tenants' entries to the caller's filter step."""
    q = {"company_id": {"$in": company_ids}} if company_ids is not None else {}
    return list(db.post_log.find(q, {"_id": 0}).sort("timestamp", -1))


def update_post_engagement(company_id: str, post_urn: str, engagement: dict) -> bool:
    """Targeted engagement update for one log entry. Replaces the old
    delete_many + insert_many full-collection rewrite, which any single user
    could trigger and which destroyed everyone's history on a concurrent call
    or a crash between the delete and the insert."""
    r = db.post_log.update_one(
        {"company_id": company_id, "post_urn": post_urn},
        {"$set": {"engagement": engagement}})
    return r.matched_count > 0
