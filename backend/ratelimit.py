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
