from bs4 import BeautifulSoup
from youtube_transcript_api import YouTubeTranscriptApi
from youtube_transcript_api import (
    AgeRestricted, InvalidVideoId, IpBlocked, NoTranscriptFound, PoTokenRequired,
    RequestBlocked, TranscriptsDisabled, VideoUnavailable, VideoUnplayable,
)
import re
from urllib.parse import urlparse

import requests

import net_guard


def process_text(text: str) -> str:
    return text.strip()


def _site_name(url: str) -> str:
    host = (urlparse(url).hostname or "that site").lower()
    return host[4:] if host.startswith("www.") else host


def _url_fetch_error(exc: Exception, url: str) -> str:
    """Name the real reason a page could not be read.

    Every failure here used to surface as "Could not read that URL. Try pasting
    the article text instead." — which reads like a temporary glitch worth
    retrying, even when the site blocks automated readers outright and no number
    of retries will ever work.
    """
    site = _site_name(url)
    if isinstance(exc, net_guard.UnsafeURLError):
        return str(exc)
    if isinstance(exc, requests.exceptions.HTTPError) and exc.response is not None:
        code = exc.response.status_code
        if code in (401, 402, 403, 451):
            return (f"{site} blocks automated readers, so Voyce can't fetch this article. "
                    f"Many news publishers do. Open it, copy the text, and use Paste Text "
                    f"instead — that works.")
        if code == 404:
            return f"That page doesn't exist on {site} any more. Check the link."
        if code == 429:
            return (f"{site} is rate-limiting us right now. Wait a few minutes and try "
                    f"again, or paste the text directly.")
        if 500 <= code < 600:
            return (f"{site} is having server problems right now ({code}). Try again "
                    f"shortly, or paste the text directly.")
        return (f"{site} refused the request ({code}). Paste the article text directly "
                f"instead.")
    if isinstance(exc, requests.exceptions.Timeout):
        return f"{site} took too long to respond. Try again, or paste the text directly."
    if isinstance(exc, requests.exceptions.SSLError):
        return f"{site} has a certificate problem, so we won't fetch from it. Paste the text directly."
    if isinstance(exc, requests.exceptions.ConnectionError):
        return f"Couldn't reach {site}. Check the link, or paste the text directly."
    return "Could not read that URL. Try pasting the article text instead."


def process_url(url: str) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        response = net_guard.safe_get(url, headers=headers, timeout=10)
        response.raise_for_status()
    except Exception as exc:
        # ValueError so the API layer passes the specific reason to the user
        # rather than replacing it with the generic wrapper.
        raise ValueError(_url_fetch_error(exc, url)) from exc

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


# English first, because the posts are written in English. Anything else the
# video actually has is accepted and translated by YouTube where the track
# allows it — a Hindi video with translatable Hindi captions used to fail
# outright, because the library is only ever asked for ("en",) by default.
_PREFERRED_LANGS = ("en", "en-US", "en-GB")


def _youtube_error(exc: Exception) -> str:
    """Say which of the possible causes it actually was.

    The old handler caught bare Exception and printed a list of four guesses,
    so a video whose captions are simply in another language read as "captions
    turned off, private, age-restricted, or blocked" — none of which was true.
    """
    if isinstance(exc, TranscriptsDisabled):
        return ("This video has captions turned off, so there is no transcript to read. "
                "Try another video, or paste the text directly.")
    if isinstance(exc, (VideoUnavailable, InvalidVideoId, VideoUnplayable)):
        return ("That video is unavailable — it may be private, deleted, or region-locked. "
                "Check the link, or paste the text directly.")
    if isinstance(exc, AgeRestricted):
        return ("That video is age-restricted, so YouTube will not serve its transcript. "
                "Try another video, or paste the text directly.")
    if isinstance(exc, (IpBlocked, RequestBlocked, PoTokenRequired)):
        # Cloud hosts get blocked far more than home connections, so this one is
        # about the server, not the video, and the user can do nothing about it.
        return ("YouTube is blocking transcript requests from our server right now. "
                "This is on our side, not your video — paste the text directly and it "
                "will work.")
    if isinstance(exc, NoTranscriptFound):
        return ("This video has no caption track we can read, in any language. "
                "Try another video, or paste the text directly.")
    return ("Couldn't read this video's transcript. Try another video, or paste the "
            "text directly.")


def _pick_transcript(transcript_list):
    """The best caption track available, translated to English when needed.

    Preference: a real English track, then manually written captions in any
    language, then auto-generated ones. Returns (transcript, source_language)
    where source_language is empty when it was English to begin with.
    """
    try:
        return transcript_list.find_transcript(list(_PREFERRED_LANGS)), ""
    except NoTranscriptFound:
        pass

    available = list(transcript_list)
    if not available:
        raise NoTranscriptFound(transcript_list.video_id, list(_PREFERRED_LANGS), transcript_list)

    # Human-written captions are markedly cleaner than auto-generated ones.
    available.sort(key=lambda t: t.is_generated)
    chosen = available[0]

    if chosen.is_translatable and any(
        getattr(lang, "language_code", "") == "en" for lang in chosen.translation_languages
    ):
        try:
            return chosen.translate("en"), chosen.language
        except Exception:
            pass   # fall through and use the original track untranslated

    return chosen, chosen.language


def _youtube_via_gemini(url: str, caption_error: Exception) -> str:
    """Fallback when the captions cannot be fetched: let Gemini watch the video.

    Gemini writes a faithful rendition rather than a verbatim transcript, so it
    is labelled as such — otherwise the generator would quote it as if those
    were the speaker's exact words.
    """
    try:
        from llm import read_youtube
        body = read_youtube(url).strip()
    except Exception as exc:
        print(f"[youtube] Gemini could not read {url} either: "
              f"{type(exc).__name__}: {exc}", flush=True)
        # Report the original caption failure — it is the more specific of the two.
        raise ValueError(_youtube_error(caption_error)) from exc

    if len(body) < 80:
        raise ValueError(
            "There was not enough spoken content in that video to write a post from. "
            "Try a longer one, or paste the text directly."
        )
    note = ("[Watched and summarised by AI because this video's captions could not be "
            "fetched. Treat it as a faithful account of the content, not as the "
            "speaker's exact words — do not quote it verbatim.]")
    return (note + chr(10) * 2 + body)[:8000]


def process_youtube(url: str) -> str:
    video_id = extract_youtube_id(url)
    if not video_id:
        raise ValueError(
            "Could not read a YouTube video ID from that link. "
            "Paste a full watch URL like https://www.youtube.com/watch?v=..."
        )

    # Captions first: instant, free, and the speaker's exact words. It talks to
    # YouTube from our server though, and YouTube blocks cloud-provider IPs, so
    # on Render this leg fails for every video. Gemini picks it up from there.
    try:
        transcript_list = YouTubeTranscriptApi().list(video_id)
        transcript, source_language = _pick_transcript(transcript_list)
        fetched = transcript.fetch()
    except Exception as exc:
        print(f"[youtube] captions unavailable for {video_id} "
              f"({type(exc).__name__}); asking Gemini to watch it", flush=True)
        return _youtube_via_gemini(url, exc)

    parts = [(getattr(s, "text", None) or (s.get("text", "") if isinstance(s, dict) else ""))
             for s in fetched]
    full_text = " ".join(p for p in parts if p).strip()
    if len(full_text) < 40:
        raise ValueError(
            "This video's transcript is too short to write a post from. "
            "Try a longer video, or paste the text directly."
        )

    # Tell the model the transcript was translated, so it does not treat
    # translation artefacts as the speaker's voice.
    if source_language:
        note = (f"[Transcript machine-translated from {source_language}. "
                f"The phrasing is the translator's, not the speaker's.]")
        full_text = note + chr(10) * 2 + full_text
    return full_text[:8000]  # cap to ~8k chars


def extract_youtube_id(url: str) -> str | None:
    # /shorts/ and /live/ were missing, so a pasted Short was rejected as "not a
    # YouTube link" even though it is one and usually has captions.
    patterns = [
        r"(?:v=|youtu\.be/)([A-Za-z0-9_-]{11})",
        r"(?:embed/|shorts/|live/|/v/)([A-Za-z0-9_-]{11})",
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
