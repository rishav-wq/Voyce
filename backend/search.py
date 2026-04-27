import os
import requests
from dotenv import load_dotenv

load_dotenv()

TAVILY_API_KEY = os.getenv("TAVILY_API_KEY")


def search_industry_news(industry: str, company_name: str, num_results: int = 5) -> list[dict]:
    if not TAVILY_API_KEY:
        return []

    query = f"latest trends news {industry} {company_name} 2026"

    response = requests.post(
        "https://api.tavily.com/search",
        json={
            "api_key": TAVILY_API_KEY,
            "query": query,
            "search_depth": "basic",
            "max_results": num_results,
            "include_answer": True,
        },
        timeout=10,
    )
    response.raise_for_status()
    data = response.json()

    results = []
    for r in data.get("results", []):
        results.append({
            "title": r.get("title", ""),
            "snippet": r.get("content", "")[:300],
            "url": r.get("url", ""),
        })

    return results


def format_news_context(results: list[dict]) -> str:
    if not results:
        return ""
    lines = ["Recent industry news and trends:"]
    for i, r in enumerate(results, 1):
        lines.append(f"{i}. {r['title']}: {r['snippet']}")
    return "\n".join(lines)
