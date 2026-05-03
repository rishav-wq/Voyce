"""
hooks.py — Dedicated scroll-stopping hook engine.

Two-phase approach:
  Phase 1: Generate 3 hook candidates, each using a different formula.
  Phase 2: Pick the sharpest one (Groq judges its own output).

Returns a single, locked hook string ready to inject as line 1 of any post or carousel.
"""

import os
import json
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
_groq = Groq(api_key=os.getenv("GROQ_API_KEY"))

# ── The 5 formulas with examples ──────────────────────────────────────────────

HOOK_FORMULAS = {
    "specific_number": {
        "name": "Specific Number",
        "template": "[X]% of [audience] [surprising fact]. Not [wrong assumption]. [Real cause].",
        "examples": [
            "43% of AI projects fail at deployment. Not ideation. Deployment.",
            "78% of cold emails are never opened. The subject line isn't the problem.",
            "Only 3% of LinkedIn posts get 80% of total feed impressions.",
        ],
    },
    "named_event": {
        "name": "Named Event",
        "template": "[Company/Person] just [did X]. The implication nobody is talking about: [Y].",
        "examples": [
            "Salesforce just cut 1,000 jobs. AI replaced them in 90 days.",
            "Google just made Gemini free for all Workspace users. Here's what that actually means for SaaS.",
            "McKinsey's latest report says 30% of tasks will be automated by 2030. The jobs they listed surprised me.",
        ],
    },
    "counterintuitive": {
        "name": "Counterintuitive Truth",
        "template": "The [best/top/smartest] [people] in [field] [do the opposite of what everyone expects].",
        "examples": [
            "The best engineers I know hate documentation. That's exactly why their code is readable.",
            "The top 1% of LinkedIn creators post 2x per week, not daily.",
            "The most successful founders I know never pitch their idea first.",
        ],
    },
    "confession": {
        "name": "Uncomfortable Confession",
        "template": "I [specific mistake] for [timeframe]. Here's what I missed.",
        "examples": [
            "I hired for skills for 3 years. Every bad hire had the same thing in common.",
            "I spent $40k on ads before realising our landing page had a 94% exit rate.",
            "I built the wrong product for 6 months. The customer told me on day 1. I didn't listen.",
        ],
    },
    "myth_buster": {
        "name": "Myth-Buster",
        "template": "Everyone says [X]. [The data / I've seen] the opposite.",
        "examples": [
            "Everyone says post every day on LinkedIn. The data says 3x a week outperforms daily by 40%.",
            "Everyone says cold outreach is dead. Our team closed $2M last quarter from cold DMs.",
            "Everyone says AI will replace developers. The developers I know are charging 3x more.",
        ],
    },
}

# ── Which formula fits which post type ────────────────────────────────────────

POST_TYPE_FORMULA = {
    "trend_commentary":  "named_event",
    "trend_reaction":    "named_event",
    "industry_stat":     "specific_number",
    "stat_reaction":     "specific_number",
    "expert_insight":    "counterintuitive",
    "expert_insight_p":  "counterintuitive",
    "hot_take":          "myth_buster",
    "lesson_learned":    "confession",
    "personal_story":    "confession",
    "product_spotlight": "specific_number",
    "case_study":        "confession",
}

_HOOK_GEN_SYSTEM = """You are a LinkedIn hook specialist. You write the single most important line of any post: the hook.

Your hook must:
1. Use the exact formula provided — no deviating
2. Be completely standalone — it must make total sense with zero context
3. Contain at least one SPECIFIC element: a real number, a real company name, a real event, or a real named person
4. Create an open loop — the reader must feel they need to read more to resolve a tension
5. Be under 15 words (shorter is almost always better)

Return ONLY a JSON object: {"hook": "..."}"""

_HOOK_PICK_SYSTEM = """You are a ruthless LinkedIn hook judge. You've seen 100,000 posts. You know exactly what stops a scroll.

Rate each hook on:
- Specificity (does it name a real thing?)
- Tension (does it create an open loop?)
- Surprise (does it contradict expectations?)
- Brevity (under 15 words?)

Pick the single best hook. Return ONLY a JSON object: {"winner": "...", "reason": "..."}"""


def _build_formula_prompt(formula_key: str, context: str, industry: str) -> str:
    f = HOOK_FORMULAS[formula_key]
    examples = "\n".join(f"  • {e}" for e in f["examples"])
    return f"""Formula: {f['name']}
Template: {f['template']}
Examples of this formula done well:
{examples}

Content/context to draw from:
{context[:800]}

Industry: {industry}

Write ONE hook using this formula. Extract the most surprising or counterintuitive angle from the content.
The hook must be under 15 words. Must contain a specific fact, number, or named entity."""


def generate_hook(context: str, industry: str, post_type: str = "", allowed_hooks: list[str] = None) -> str:
    """
    Phase 1: Generate hook candidates using different formulas.
    Phase 2: Pick the sharpest one.
    Returns the winning hook string.
    """
    if allowed_hooks is None:
        allowed_hooks = []
        
    all_formulas = list(HOOK_FORMULAS.keys())
    # If the user selected specific hooks, only use those. Otherwise use all.
    available_formulas = [f for f in all_formulas if f in allowed_hooks] if allowed_hooks else all_formulas

    # Pick up to 3 formulas: primary (post_type-specific if allowed) + up to 2 alternates
    primary = POST_TYPE_FORMULA.get(post_type, "counterintuitive")
    if primary not in available_formulas:
        primary = available_formulas[0] if available_formulas else "counterintuitive"

    alternates = [f for f in available_formulas if f != primary][:2]
    formula_set = [primary] + alternates
    
    # Remove duplicates
    formula_set = list(dict.fromkeys(formula_set))

    candidates = []
    for formula_key in formula_set:
        try:
            resp = _groq.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=[
                    {"role": "system", "content": _HOOK_GEN_SYSTEM},
                    {"role": "user", "content": _build_formula_prompt(formula_key, context, industry)},
                ],
                response_format={"type": "json_object"},
                max_tokens=120,
                temperature=0.85,  # slightly higher for diversity
            )
            hook = json.loads(resp.choices[0].message.content).get("hook", "").strip()
            if hook:
                candidates.append({"formula": HOOK_FORMULAS[formula_key]["name"], "hook": hook})
        except Exception:
            continue

    if not candidates:
        return ""  # fallback: let the main prompt handle it

    if len(candidates) == 1:
        return candidates[0]["hook"]

    # Phase 2: Pick the winner
    try:
        candidates_text = "\n".join(
            f"{i+1}. [{c['formula']}] {c['hook']}"
            for i, c in enumerate(candidates)
        )
        pick_resp = _groq.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=[
                {"role": "system", "content": _HOOK_PICK_SYSTEM},
                {"role": "user", "content": f"Industry: {industry}\n\nHooks to judge:\n{candidates_text}"},
            ],
            response_format={"type": "json_object"},
            max_tokens=150,
        )
        winner = json.loads(pick_resp.choices[0].message.content).get("winner", "").strip()
        if winner:
            return winner
    except Exception:
        pass

    # Fallback: return primary formula candidate
    return candidates[0]["hook"]
