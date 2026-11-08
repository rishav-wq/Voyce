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
import re
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


# For a person's profile the generic angles above pulled in unrelated stories ("open source",
# "US market" beside "executive coaching"); these suit a professional's niche.
_PRO_ANGLES = ["survey", "research report", "case study", "CEO interview", "leadership", "trend", "new study"]

# Words that mark a sports story. "Coaching", "team" and "season" are business words too, so a
# search for top teams or coaching kept returning NBA predictions and cricket coaches.
_SPORTS = re.compile(r"\b(nba|nfl|mlb|nhl|ipl|bcci|ecb|cricket|wicket|football|soccer|premier league|la liga|rugby|"
                     r"olympics?|fifa|uefa|espn|cricbuzz|playoffs?|quarterback|touchdown|super bowl|world cup|"
                     r"grand slam|formula 1|f1|athletes?|sports?)\b", re.I)


# Job ads, careers pages and job boards. A partner-ecosystem search came back as CGI, NetApp and
# Neo4j openings and a LinkedIn "Chief AI Officer" listing, and the writer treated them as news.
_LISTING = re.compile(r"\b(jobs?|careers?|hiring|vacanc\w*|apply now|job boards?|job application|we're looking for)\b"
                      r"|/jobs?/|/careers?/|careers\.|jobs\.|njoyn|greenhouse\.io|ashbyhq|lever\.co|workday|"
                      r"linkedin\.com/jobs|naukri|indeed\.|glassdoor|remoterocketship|builtin\w*\.com/job", re.I)


# Words in a search phrase that say nothing about what the story is about (see search_industry_news)
_GENERIC = {"india", "indian", "inc", "global", "new", "the", "and", "for", "with", "launch", "launches", "changes",
            "change", "growth", "announced", "announcement", "companies", "company", "results", "planning", "leadership",
            "business", "businesses", "india's", "plan", "plans", "program", "programme", "programs", "update"}

# Social media posts are someone's post, not news (a LinkedIn "post" that was a job ad, an Instagram reel)
_SOCIAL = re.compile(r"linkedin\.com/(posts|pulse|feed)|instagram\.com|facebook\.com|//(www\.)?(x|twitter)\.com/|"
                     r"youtube\.com|youtu\.be|tiktok\.com|reddit\.com|quora\.com|threads\.(net|com)", re.I)


def _screen(topic: str, query: str, items: list) -> list:
    """Claude keeps only the results that are real news or reports on the topic. Words alone can't
    tell a story from a help page, a course advert or an unrelated local story that shares a word.
    The checker model at low effort, well under ₹1 a search; on any failure the results stay as they are."""
    if not items:
        return items
    from llm import quick_json
    lines = "\n".join(f"{i}. {r.get('title', '')} — {(r.get('content') or r.get('snippet') or '')[:220]}"
                      for i, r in enumerate(items))
    v = quick_json(f"""Topic: {topic}
Search: {query}
Which of these are real news, announcements, data or reports about the topic, that someone who writes
on LinkedIn about it could react to? Not: product documentation, help pages, courses or adverts,
generic how-to guides, stories that only share a word with the topic.
{lines}
Return ONLY JSON: {{"keep": [the numbers of the ones to keep]}}""")
    keep = (v or {}).get("keep")
    if not isinstance(keep, list):
        return items
    return [items[i] for i in keep if isinstance(i, int) and 0 <= i < len(items)]


# Paid press-release wires for market-research reports ("Coaching Platform Market to Reach USD 12.22
# Billion by 2036"): numbers no one can check, written to sell a report
_PR_SPAM = re.compile(r"openpr\.com|einpresswire\.com|einnews\.com/pr_news|market\.us/|marketresearch\.com|"
                      r"\bmarket (size|to (reach|witness|grow|hit))\b|\bCAGR\b", re.I)


def _off_topic(r: dict, topic: str) -> bool:
    """A sports story, unless the topic itself is about sport; any job ad, social media post or
    market-research press release."""
    where = " ".join((r.get("title", ""), r.get("url", "")))
    if _LISTING.search(where) or _SOCIAL.search(r.get("url", "")) or _PR_SPAM.search(where):
        return True
    if _SPORTS.search(topic or ""):
        return False
    return bool(_SPORTS.search(" ".join((r.get("title", ""), r.get("url", ""), (r.get("content") or "")[:200]))))


_NEWS_TYPES = {"trend_commentary", "trend_reaction", "product_spotlight"}
_STAT_TYPES = {"industry_stat", "stat_reaction"}
# A take on what's happening now: the past month first, wider only when that finds too little. Searched
# without a date, Raj's first hot take (8 Nov 2026) was built on a 2024 article, against "this week's news".
_FRESH_TYPES = {"hot_take", "prediction"}


def _tavily(query: str, depth: str, num_results: int, **filters) -> dict:
    response = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_API_KEY,
            "query": query,
            "search_depth": depth,
            "max_results": num_results,
            "include_answer": False,   # Tavily's own summary isn't a source; see search_industry_news
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
    region: str = "",
    context: str = "",
    queries: list[str] | None = None,
) -> list[dict]:
    """region: where the author works ("India"), so the news is theirs; context: words that keep
    an ambiguous topic in the business world ("business workplace" for a person). queries: search
    phrases written for this topic the way headlines word it (autonomous.topic_queries); one is
    used as it is, without the templates, region or context, which turned a topic like "Hyperscaler
    and marketplace co-sell" into job ads and workplace reports."""
    if not TAVILY_API_KEY:
        return []

    year = datetime.now(timezone.utc).year
    phrases = [q.strip() for q in (queries or []) if isinstance(q, str) and q.strip()]
    if phrases:
        query = random.choice(phrases)
    else:
        # Pick a query template based on post type
        templates = _QUERY_TEMPLATES.get(post_type, _DEFAULT_QUERIES)
        template = random.choice(templates)

        # Add a random diversity angle to break repetition. Product-level angles
        # (e.g. "LinkedIn algorithm change", "creator monetization" for a content product)
        # join the generic pool so each product's news stays niche-appropriate.
        angle = random.choice((_PRO_ANGLES if context else _DIVERSITY_ANGLES) + [a for a in (extra_angles or []) if a])
        query = " ".join(x for x in (template.format(industry=industry, year=year), context, region, angle) if x).strip()

    # A post that reacts to news must not treat an old story as new: those
    # types search news only, from the past week. Stats may come from this
    # year's reports. Ideas, lessons and stories aren't dated, so they search
    # everything.
    if post_type in _STAT_TYPES:
        window = {"time_range": "year"}
    elif post_type in _FRESH_TYPES:
        window = {"time_range": "month"}
    elif post_type in _NEWS_TYPES or post_type not in _QUERY_TEMPLATES:
        window = {"topic": "news", "time_range": "week"}
    else:
        window = {}
    fresh = post_type in _FRESH_TYPES

    # A few more than asked for: job ads, sports, social posts and repeats are dropped after the search
    fetch = num_results + 4
    news_only = window.get("topic") == "news"

    def usable(d: dict, q: str) -> list:
        # A phrase's own words keep a story on it: "partner program overhaul OEM" brought back a
        # theme-park maintenance feature and an aero-engine earnings call
        anchors = {w for w in re.findall(r"[a-z0-9][a-z0-9-]{2,}", q.lower()) if w not in _GENERIC} if phrases else set()
        out, seen = [], set()
        for r in d.get("results", []):
            if _off_topic(r, industry):
                continue
            title = (r.get("title") or "").lower()
            body = (r.get("content") or r.get("snippet") or "")[:700].lower()
            if anchors and not (any(a in title for a in anchors) or sum(a in body for a in anchors) >= 2):
                continue
            if news_only and not r.get("published_date"):   # a "this week" search: an undated page isn't this week's news
                continue
            key = " ".join(re.findall(r"[a-z0-9]+", title)[:7])
            if key in seen:                                   # one story, syndicated to several sites
                continue
            seen.add(key)
            out.append(r)
        return out

    def gather(q: str) -> list:
        try:
            data = _tavily(q, "advanced", fetch, **window)
            if news_only and len(usable(data, q)) < 2:
                # A quiet week in a small niche: widen to the month rather than
                # drop the date limit.
                data = _tavily(q, "advanced", fetch, topic="news", time_range="month")
            elif fresh and len(usable(data, q)) < 2:
                # A take with too little from the past month: the past year, and only then anything
                data = _tavily(q, "advanced", fetch, time_range="year")
                if len(usable(data, q)) < 2:
                    data = _tavily(q, "advanced", fetch)
        except Exception:
            # Fallback: a simpler query, still recent
            try:
                data = _tavily(f"{industry} latest news {year}", "basic", fetch, time_range="month")
            except Exception:
                return []
        return _screen(industry, q, usable(data, q))

    found = gather(query)
    others = [q for q in phrases if q != query]
    if len(found) < 2 and others:   # too little real news for this phrase: try another of the topic's phrases
        urls = {r.get("url") for r in found}
        found += [r for r in gather(random.choice(others)) if r.get("url") not in urls]

    # Only real articles. Tavily's own AI-written summary used to lead the list as a source with no
    # link, and writers took facts from it that no page says.
    results = []
    for r in found:
        snippet = r.get("content", "") or r.get("snippet", "")
        results.append({
            "title": re.sub(r"<!--.*?-->|\s+", " ", r.get("title") or "").strip(),   # Storyboard18 titles carry "<!-- -->"
            "snippet": snippet[:600],   # more context for LLM specificity
            "url": r.get("url", ""),
            "published": (r.get("published_date") or "")[:16],
        })

    return results[:num_results]


def format_news_context(results: list[dict]) -> str:
    if not results:
        return ""
    lines = ["Real-time industry context (use specific facts, numbers, and company names from these):"]
    for i, r in enumerate(results, 1):
        source = f" ({r['url']})" if r.get("url") else ""
        when = f" — published {r['published']}" if r.get("published") else ""
        lines.append(f"\n[{i}] {r['title']}{source}{when}\n{r['snippet']}")
    return "\n".join(lines)
