import requests
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
import re


def process_text(text: str) -> str:
    return text.strip()


def process_url(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Meta fallbacks — these are usually server-rendered even on JS apps / SPAs,
    # so a page with no scrapable <body> text can still yield a title + summary.
    def _meta(*candidates):
        for attr, val in candidates:
            tag = soup.find("meta", {attr: val})
            if tag and tag.get("content"):
                return tag["content"].strip()
        return ""

    title = (soup.title.get_text(strip=True) if soup.title else "") or \
        _meta(("property", "og:title"), ("name", "twitter:title"))
    description = _meta(("name", "description"), ("property", "og:description"),
                        ("name", "twitter:description"))

    # Remove noise, then pull the main readable text
    for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
        tag.decompose()
    content = soup.find("article") or soup.find("main") or soup.find("body")
    text = content.get_text(separator="\n", strip=True) if content else ""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    body_text = "\n".join(lines[:200])  # cap to stay within token limits

    # Use the meta description only to enrich real body text — never as the sole
    # source (a bare title would make the model hallucinate a post from nothing).
    meaningful = body_text if len(body_text) >= 200 else (description or body_text)
    if len(meaningful.strip()) < 120:
        raise ValueError(
            "That link has no readable text to work from — it looks like a web app or "
            "login page that renders its content with JavaScript, so there's nothing to "
            "read. Try a blog post, article, or marketing-page URL, or paste the text directly."
        )
    return (title + "\n\n" + meaningful).strip() if title else meaningful


def process_youtube(url: str) -> str:
    video_id = extract_youtube_id(url)
    if not video_id:
        raise ValueError(
            "Could not read a YouTube video ID from that link. "
            "Paste a full watch URL like https://www.youtube.com/watch?v=..."
        )

    # youtube-transcript-api changed its API in v1.0 (get_transcript → instance .fetch).
    # Support both so it works regardless of the installed version.
    try:
        if hasattr(YouTubeTranscriptApi, "get_transcript"):        # older API (<1.0)
            entries = YouTubeTranscriptApi.get_transcript(video_id)
            parts = [e.get("text", "") for e in entries]
        else:                                                       # newer API (>=1.0)
            fetched = YouTubeTranscriptApi().fetch(video_id)
            parts = [(getattr(s, "text", None)
                      or (s.get("text", "") if isinstance(s, dict) else "")) for s in fetched]
    except Exception:
        raise ValueError(
            "Couldn't get this video's transcript — it may have captions turned off, be "
            "private or age-restricted, or blocked for automated access. Try another video, "
            "or paste the text directly."
        )

    full_text = " ".join(p for p in parts if p).strip()
    if len(full_text) < 40:
        raise ValueError(
            "This video doesn't have a usable transcript. Try one with captions, "
            "or paste the text directly."
        )
    return full_text[:8000]  # cap to ~8k chars


def extract_youtube_id(url: str) -> str | None:
    patterns = [
        r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:embed/)([A-Za-z0-9_-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def process_input(input_type: str, content: str) -> str:
    if input_type == "text":
        return process_text(content)
    elif input_type == "url":
        return process_url(content)
    elif input_type == "youtube":
        return process_youtube(content)
    else:
        raise ValueError(f"Unknown input type: {input_type}")
