"""SSRF guard for outbound fetches of user-supplied URLs.

Any endpoint that fetches a URL the user gave us (article scraping, company
website analysis) must go through safe_get / safe_head instead of calling
requests directly. These validate that the URL is http(s) and that its host
does NOT resolve to a private, loopback, link-local, or otherwise non-public
address — which is what stops a signed-up user from turning our server into a
proxy to internal services or the cloud metadata endpoint.

Redirects are followed manually so every hop is re-validated (a public URL that
302s to http://169.254.169.254 is caught).

Residual risk: DNS rebinding between the resolve check and the actual socket
connect (TOCTOU) is not fully closed here — doing so requires pinning the
resolved IP into the connection. Acceptable for the current single-instance
deployment; revisit if we handle untrusted URLs at higher volume.
"""

import ipaddress
import socket
from urllib.parse import urljoin, urlparse

import requests

MAX_REDIRECTS = 5


class UnsafeURLError(ValueError):
    """Raised when a URL is not allowed to be fetched (bad scheme or private IP)."""


def _ip_is_public(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _assert_safe(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise UnsafeURLError("Only http and https URLs are allowed.")
    host = parsed.hostname
    if not host:
        raise UnsafeURLError("That URL has no host.")
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise UnsafeURLError("That URL could not be resolved.")
    if not infos:
        raise UnsafeURLError("That URL could not be resolved.")
    for info in infos:
        ip = info[4][0]
        if not _ip_is_public(ip):
            raise UnsafeURLError("That URL points to a private or internal address.")


def _safe_request(method: str, url: str, *, headers=None, timeout: float = 10, **kwargs):
    # We manage redirects ourselves so each hop is re-validated.
    kwargs.pop("allow_redirects", None)
    current = url
    for _ in range(MAX_REDIRECTS + 1):
        _assert_safe(current)
        resp = requests.request(
            method, current, headers=headers, timeout=timeout,
            allow_redirects=False, **kwargs
        )
        if resp.is_redirect:
            location = resp.headers.get("location")
            if not location:
                return resp
            current = urljoin(current, location)
            continue
        return resp
    raise UnsafeURLError("Too many redirects.")


def safe_get(url: str, **kwargs):
    return _safe_request("GET", url, **kwargs)


def safe_head(url: str, **kwargs):
    return _safe_request("HEAD", url, **kwargs)
