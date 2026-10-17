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
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")

# ── Per-post-type search angle templates ─────────────────────────────────────
# Each produces a different news angle so runs on the same day stay diverse.

_QUERY_TEMPLATES = {
    "trend_commentary":  [
        "breaking news {industry} {year}",
        "latest {industry} industry shift announcement",
        "{industry} new development this week",
    ],
    "trend_reaction":    [
        "biggest {industry} news this month",
        "recent {industry} announcement controversy",
        "{industry} latest report finding {year}",
    ],
    "industry_stat":     [
        "{industry} statistics data report {year}",
        "{industry} research findings percentage survey",
        "{industry} market size growth numbers latest",
    ],
    "stat_reaction":     [
        "{industry} surprising data study result",
        "{industry} research report key statistic",
        "{industry} benchmark report {year} findings",
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
        "{industry} controversy debate opinion {year}",
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
        "{industry} product launch innovation {year}",
        "new {industry} tool solution company",
        "{industry} software feature update announcement",
    ],
    "case_study":        [
        "{industry} success story results metrics",
        "{industry} customer outcome case study {year}",
        "{industry} company transformation results",
    ],
}

_DEFAULT_QUERIES = [
    "latest {industry} trends {year}",
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


_NEWS_TYPES = {"trend_commentary", "trend_reaction", "product_spotlight"}
_STAT_TYPES = {"industry_stat", "stat_reaction"}


def _tavily(query: str, depth: str, num_results: int, **filters) -> dict:
    response = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_API_KEY,
            "query": query,
            "search_depth": depth,
            "max_results": num_results,
            "include_answer": True,
            "include_raw_content": False,
            **filters,
        },
        timeout=12,
    )
    response.raise_for_status()
    return response.json()


def search_industry_news(
    industry: str,
    company_name: str,
    num_results: int = 5,
    post_type: str = "",
    extra_angles: list[str] | None = None,
) -> list[dict]:
    if not TAVILY_API_KEY:
        return []

    # Pick a query template based on post type
    templates = _QUERY_TEMPLATES.get(post_type, _DEFAULT_QUERIES)
    template = random.choice(templates)

    # Add a random diversity angle to break repetition. Product-level angles
    # (e.g. "LinkedIn algorithm change", "creator monetization" for a content product)
    # join the generic pool so each product's news stays niche-appropriate.
    angle = random.choice(_DIVERSITY_ANGLES + [a for a in (extra_angles or []) if a])
    year = datetime.now(timezone.utc).year
    query = f"{template.format(industry=industry, year=year)} {angle}".strip()

    # A post that reacts to news must not treat an old story as new: those
    # types search news only, from the past week. Stats may come from this
    # year's reports. Ideas, lessons and stories aren't dated, so they search
    # everything.
    if post_type in _STAT_TYPES:
        window = {"time_range": "year"}
    elif post_type in _NEWS_TYPES or post_type not in _QUERY_TEMPLATES:
        window = {"topic": "news", "time_range": "week"}
    else:
        window = {}

    try:
        data = _tavily(query, "advanced", num_results, **window)
        if window.get("topic") == "news" and len(data.get("results", [])) < 2:
            # A quiet week in a small niche: widen to the month rather than
            # drop the date limit.
            data = _tavily(query, "advanced", num_results, topic="news", time_range="month")
    except Exception:
        # Fallback: a simpler query, still recent
        try:
            data = _tavily(f"{industry} latest news {year}", "basic", num_results, time_range="month")
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
            "published": (r.get("published_date") or "")[:16],
        })

    return results[:num_results + 1]   # +1 for the answer entry


def format_news_context(results: list[dict]) -> str:
    if not results:
        return ""
    lines = ["Real-time industry context (use specific facts, numbers, and company names from these):"]
    for i, r in enumerate(results, 1):
        source = f" ({r['url']})" if r.get("url") else ""
        when = f" — published {r['published']}" if r.get("published") else ""
        lines.append(f"\n[{i}] {r['title']}{source}{when}\n{r['snippet']}")
    return "\n".join(lines)
