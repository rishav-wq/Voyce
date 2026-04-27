import requests
from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
import re


def process_text(text: str) -> str:
    return text.strip()


def process_url(url: str) -> str:
    headers = {"User-Agent": "Mozilla/5.0"}
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()

    soup = BeautifulSoup(response.text, "html.parser")

    # Remove noise
    for tag in soup(["script", "style", "nav", "footer", "header", "aside"]):
        tag.decompose()

    # Try article/main first, fall back to body
    content = soup.find("article") or soup.find("main") or soup.find("body")
    text = content.get_text(separator="\n", strip=True) if content else ""

    # Collapse blank lines
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines[:200])  # cap at ~200 lines to stay within token limits


def process_youtube(url: str) -> str:
    video_id = extract_youtube_id(url)
    if not video_id:
        raise ValueError("Could not extract YouTube video ID from URL")

    transcript = YouTubeTranscriptApi.get_transcript(video_id)
    full_text = " ".join([entry["text"] for entry in transcript])
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
