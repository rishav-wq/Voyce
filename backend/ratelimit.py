"""Minimal in-memory sliding-window rate limiter.

Guards cost-incurring endpoints (LLM calls) and unauthenticated spam targets
against a single actor hammering them. Keyed by whatever caller passes —
per-user for authenticated routes, per-IP for public ones.

Note: state is in-process, so limits are per-instance. That matches the current
single-instance Render deployment (OAuth state is in-memory too). When we scale
to multiple instances, move this to a shared store (Redis) so limits are global.
"""

import threading
import time
from collections import defaultdict, deque

_lock = threading.Lock()
_hits: dict[str, deque] = defaultdict(deque)


def allow(key: str, limit: int, window: float = 60.0) -> bool:
    """Return True if this hit is within `limit` per `window` seconds, else False."""
    now = time.time()
    with _lock:
        dq = _hits[key]
        cutoff = now - window
        while dq and dq[0] <= cutoff:
            dq.popleft()
        if len(dq) >= limit:
            return False
        dq.append(now)
        # Opportunistic cleanup so idle keys don't accumulate forever.
        if len(_hits) > 10000:
            _prune(cutoff)
        return True


def _prune(cutoff: float) -> None:
    for k in [k for k, v in _hits.items() if not v or v[-1] <= cutoff]:
        _hits.pop(k, None)


# voyce.co.in is behind Cloudflare, which overwrites CF-Connecting-IP with the real visitor. Anything else
# (a partner's address, Render's own voyce-1.onrender.com) reaches Render directly, where a client can send
# that header itself, so it is trusted only here (security review, 3 Nov 2026).
_CLOUDFLARE_HOSTS = {"voyce.co.in", "www.voyce.co.in"}


def visitor_ip(request) -> str:
    """The visitor's own address, as it arrived (meta_ads sends it to Meta with an event)."""
    host = (request.headers.get("host") or "").split(":")[0].strip().lower()
    ip = request.headers.get("cf-connecting-ip", "").strip() if host in _CLOUDFLARE_HOSTS else ""
    if not ip:
        # Render's proxy appends the address it was connected from, so the LAST entry is real; the first is
        # whatever the client typed.
        fwd = [p.strip() for p in (request.headers.get("x-forwarded-for") or "").split(",") if p.strip()]
        ip = fwd[-1] if fwd else (request.client.host if request.client else "")
    return ip


def client_ip(request) -> str:
    """The visitor's address for per-visitor limits. IPv6 is keyed on its /64: one machine holds a whole
    /64, so counting single addresses let a script reset its limit by changing one."""
    import ipaddress
    ip = visitor_ip(request)
    try:
        addr = ipaddress.ip_address(ip)
        if addr.version == 6:
            return str(ipaddress.ip_network(f"{addr}/64", strict=False))
        return str(addr)
    except ValueError:
        return ip[:64] or "unknown"
