"""
search.py — Tavily-powered real-time industry news search.

Key improvements:
- Post-type-aware queries: each post type gets a different search angle
  so consecutive runs never pull the same articles.
- Fresh angle rotation: appends a rotating angle keyword to break query repetition.
- Full snippet (600 chars) so the LLM has richer context to cite specifics.
- Tavily advanced search_depth for better result quality on pro key.
"""

import os
import random
import requests
from dotenv import load_dotenv

load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# ── Per-post-type search angle templates ─────────────────────────────────────
# Each produces a different news angle so runs on the same day stay diverse.

_QUERY_TEMPLATES = {
    "trend_commentary":  [
        "breaking news {industry} 2025 2026",
        "latest {industry} industry shift announcement",
        "{industry} new development this week",
    ],
    "trend_reaction":    [
        "biggest {industry} news this month",
        "recent {industry} announcement controversy",
        "{industry} latest report finding 2025",
    ],
    "industry_stat":     [
        "{industry} statistics data report 2025 2026",
        "{industry} research findings percentage survey",
        "{industry} market size growth numbers latest",
    ],
    "stat_reaction":     [
        "{industry} surprising data study result",
        "{industry} research report key statistic",
        "{industry} benchmark report 2025 findings",
    ],
    "expert_insight":    [
        "{industry} expert opinion best practice",
        "{industry} lessons learned case study professional",
        "what top {industry} practitioners do differently",
    ],
    "expert_insight_p":  [
        "{industry} contrarian view expert take",
        "{industry} insider perspective real experience",
        "common mistakes in {industry} expert advice",
    ],
    "hot_take":          [
        "{industry} controversy debate opinion 2025",
        "unpopular opinion {industry} professionals",
        "{industry} what everyone gets wrong",
    ],
    "lesson_learned":    [
        "{industry} failure case study lesson",
        "{industry} mistake to avoid real example",
        "{industry} what went wrong story",
    ],
    "personal_story":    [
        "{industry} career turning point story",
        "{industry} founder journey challenge",
        "{industry} real experience startup",
    ],
    "product_spotlight": [
        "{industry} product launch innovation 2025",
        "new {industry} tool solution company",
        "{industry} software feature update announcement",
    ],
    "case_study":        [
        "{industry} success story results metrics",
        "{industry} customer outcome case study 2025",
        "{industry} company transformation results",
    ],
}

_DEFAULT_QUERIES = [
    "latest {industry} trends 2025 2026",
    "{industry} news this week",
    "{industry} industry development announcement",
]

# Extra diversity angles appended to break repetition across same-day runs
_DIVERSITY_ANGLES = [
    "startup",
    "enterprise",
    "layoffs hiring",
    "funding investment",
    "regulation policy",
    "open source",
    "research paper",
    "product launch",
    "CEO announcement",
    "salary compensation",
    "India market",
    "US market",
    "future prediction",
]


def search_industry_news(
    industry: str,
    company_name: str,
    num_results: int = 5,
    post_type: str = "",
) -> list[dict]:
    if not TAVILY_API_KEY:
        return []

    # Pick a query template based on post type
    templates = _QUERY_TEMPLATES.get(post_type, _DEFAULT_QUERIES)
    template = random.choice(templates)

    # Add a random diversity angle to break repetition
    angle = random.choice(_DIVERSITY_ANGLES)
    query = f"{template.format(industry=industry)} {angle}".strip()

    try:
        response = requests.post(
            "https://api.tavily.com/search",
            json={
                "api_key": TAVILY_API_KEY,
                "query": query,
                "search_depth": "advanced",   # better quality
                "max_results": num_results,
                "include_answer": True,
                "include_raw_content": False,
            },
            timeout=12,
        )
        response.raise_for_status()
        data = response.json()
    except Exception:
        # Fallback: try a simpler query
        try:
            response = requests.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": f"{industry} latest news 2025",
                    "search_depth": "basic",
                    "max_results": num_results,
                    "include_answer": True,
                },
                timeout=10,
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            return []

    results = []

    # Include Tavily's synthesised answer as the first "result" if present
    answer = data.get("answer", "").strip()
    if answer:
        results.append({
            "title": f"Tavily synthesis — {query}",
            "snippet": answer[:500],
            "url": "",
        })

    for r in data.get("results", []):
        snippet = r.get("content", "") or r.get("snippet", "")
        results.append({
            "title": r.get("title", ""),
            "snippet": snippet[:600],   # more context for LLM specificity
            "url": r.get("url", ""),
        })

    return results[:num_results + 1]   # +1 for the answer entry


def format_news_context(results: list[dict]) -> str:
    if not results:
        return ""
    lines = ["Real-time industry context (use specific facts, numbers, and company names from these):"]
    for i, r in enumerate(results, 1):
        source = f" ({r['url']})" if r.get("url") else ""
        lines.append(f"\n[{i}] {r['title']}{source}\n{r['snippet']}")
    return "\n".join(lines)
