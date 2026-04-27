import json
import os
import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from groq import Groq
from dotenv import load_dotenv

load_dotenv()
client = Groq(api_key=os.getenv("GROQ_API_KEY"))

HEADERS = {"User-Agent": "Mozilla/5.0"}
PRIORITY_PATHS = ["/about", "/products", "/services", "/solutions",
                  "/blog", "/news", "/insights", "/resources", "/features"]


def _fetch_text(url: str, max_chars: int = 3000) -> str:
    try:
        r = requests.get(url, headers=HEADERS, timeout=8)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        content = soup.find("article") or soup.find("main") or soup.find("body")
        text = content.get_text(separator="\n", strip=True) if content else ""
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        return "\n".join(lines)[:max_chars]
    except Exception:
        return ""


def _get_sitemap_urls(base_url: str) -> list[str]:
    urls = []
    for path in ["/sitemap.xml", "/sitemap_index.xml", "/sitemap"]:
        try:
            r = requests.get(base_url.rstrip("/") + path, headers=HEADERS, timeout=6)
            if r.status_code == 200 and "xml" in r.headers.get("content-type", ""):
                root = ET.fromstring(r.text)
                ns = {"sm": "http://www.sitemaps.org/schemas/sitemap/0.9"}
                urls = [loc.text for loc in root.findall(".//sm:loc", ns) if loc.text]
                if urls:
                    break
        except Exception:
            continue
    return urls


def _discover_priority_pages(base_url: str, sitemap_urls: list[str]) -> list[str]:
    base = base_url.rstrip("/")
    domain = urlparse(base_url).netloc
    found = set()

    # From sitemap
    for url in sitemap_urls:
        path = urlparse(url).path.lower()
        for p in PRIORITY_PATHS:
            if p in path:
                found.add(url)
                break

    # Try common paths directly
    for path in PRIORITY_PATHS:
        url = base + path
        if url not in found:
            try:
                r = requests.head(url, headers=HEADERS, timeout=4, allow_redirects=True)
                if r.status_code == 200:
                    found.add(url)
            except Exception:
                pass

    # Crawl homepage links
    try:
        r = requests.get(base_url, headers=HEADERS, timeout=8)
        soup = BeautifulSoup(r.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = urljoin(base_url, a["href"])
            if urlparse(href).netloc == domain:
                path = urlparse(href).path.lower()
                for p in PRIORITY_PATHS:
                    if p in path:
                        found.add(href)
                        break
    except Exception:
        pass

    return list(found)[:8]  # cap at 8 pages


def _get_blog_posts(base_url: str, sitemap_urls: list[str]) -> list[str]:
    domain = urlparse(base_url).netloc
    blog_urls = []

    # Find blog post URLs from sitemap
    for url in sitemap_urls:
        path = urlparse(url).path.lower()
        if any(kw in path for kw in ["/blog/", "/news/", "/insights/", "/post/"]):
            blog_urls.append(url)

    # Scrape blog listing page if no sitemap posts
    if not blog_urls:
        for path in ["/blog", "/news", "/insights"]:
            try:
                listing_url = base_url.rstrip("/") + path
                r = requests.get(listing_url, headers=HEADERS, timeout=8)
                if r.status_code != 200:
                    continue
                soup = BeautifulSoup(r.text, "html.parser")
                for a in soup.find_all("a", href=True):
                    href = urljoin(listing_url, a["href"])
                    if urlparse(href).netloc == domain and path in urlparse(href).path.lower():
                        blog_urls.append(href)
                if blog_urls:
                    break
            except Exception:
                pass

    # Return top 5 most recent
    return list(dict.fromkeys(blog_urls))[:5]


def _ai_analyze(company_name: str, scraped_pages: dict) -> dict:
    pages_text = "\n\n---\n\n".join(
        f"[{label}]\n{content}"
        for label, content in scraped_pages.items()
        if content
    )

    prompt = f"""Analyze this company's website content for {company_name} and extract:

{pages_text}

Return a JSON object with these exact keys:
{{
  "description": "2-3 sentence company description",
  "products_services": ["product/service 1", "product/service 2", ...],
  "key_topics": ["topic 1", "topic 2", "topic 3", "topic 4", "topic 5"],
  "target_audience": "who they sell to",
  "unique_value": "what makes them different",
  "content_themes": ["theme for linkedin posts 1", "theme 2", "theme 3"]
}}

Return ONLY valid JSON, no explanation."""

    response = client.chat.completions.create(
        model="llama-3.3-70b-versatile",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.3,
    )

    text = response.choices[0].message.content.strip()
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        text = match.group(0)
    try:
        return json.loads(text)
    except Exception:
        return {}


def _extract_brand_color(html: str) -> str:
    """Extract primary brand color hex from page HTML meta tags."""
    try:
        soup = BeautifulSoup(html, "html.parser")
        checks = [
            ("meta", {"name": "theme-color"}),
            ("meta", {"name": "msapplication-TileColor"}),
            ("meta", {"property": "theme-color"}),
        ]
        for tag, attrs in checks:
            el = soup.find(tag, attrs)
            if el and el.get("content"):
                val = el["content"].strip()
                if re.match(r'^#[0-9a-fA-F]{3,6}$', val):
                    return val
        link = soup.find("link", {"rel": "mask-icon"})
        if link and re.match(r'^#[0-9a-fA-F]{3,6}$', link.get("color", "")):
            return link["color"].strip()
    except Exception:
        pass
    return ""


def scrape_company(base_url: str, company_name: str) -> dict:
    """Full intelligent website scrape. Returns structured company knowledge."""

    # 1. Discover sitemap
    sitemap_urls = _get_sitemap_urls(base_url)

    # 2. Discover priority pages
    priority_pages = _discover_priority_pages(base_url, sitemap_urls)

    # 3. Scrape homepage (extract brand color + text from same request)
    scraped = {}
    brand_color = ""
    try:
        homepage_r = requests.get(base_url, headers=HEADERS, timeout=8)
        homepage_r.raise_for_status()
        brand_color = _extract_brand_color(homepage_r.text)
        soup = BeautifulSoup(homepage_r.text, "html.parser")
        for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
            tag.decompose()
        content = soup.find("main") or soup.find("body")
        text = content.get_text(separator="\n", strip=True) if content else ""
        lines = [l.strip() for l in text.splitlines() if l.strip()]
        scraped["Homepage"] = "\n".join(lines)[:2000]
    except Exception:
        scraped["Homepage"] = _fetch_text(base_url, 2000)

    for url in priority_pages:
        label = urlparse(url).path.strip("/").replace("/", " › ") or "page"
        scraped[label] = _fetch_text(url, 1500)

    # 4. Scrape recent blog posts
    blog_posts = _get_blog_posts(base_url, sitemap_urls)
    blog_texts = []
    for url in blog_posts:
        text = _fetch_text(url, 800)
        if text:
            blog_texts.append(text)
    if blog_texts:
        scraped["Recent Blog Posts"] = "\n\n---\n".join(blog_texts)

    # 5. AI analysis
    analysis = _ai_analyze(company_name, scraped)

    return {
        "raw_pages": scraped,
        "analysis": analysis,
        "pages_scraped": len(scraped),
        "blog_posts_found": len(blog_posts),
        "brand_color": brand_color,
    }
