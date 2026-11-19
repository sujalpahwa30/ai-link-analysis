"""Sliding-window rate limiting for the expensive analysis endpoint.

The analysis endpoint spends a provider quota per request, so a public
deployment needs a per-client ceiling. This limiter is intentionally
in-process: it is accurate for a single instance, and a multi-instance
deployment should move the counters to Redis.
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Mapping


def resolve_client_key(headers: Mapping[str, str], socket_host: str | None) -> str:
    """Return a stable client key that a caller cannot choose for themselves.

    Behind Cloudflare, ``CF-Connecting-IP`` is written by the edge and any
    caller-supplied copy is overwritten, so it is the only address worth
    trusting. ``X-Forwarded-For`` is deliberately not used: its left-most
    entries come from the caller, and keying on them lets a client rotate the
    header to escape the limit. The socket peer is the fallback for local runs
    and for hosts that terminate TLS directly.
    """

    for name, value in headers.items():
        if name.lower() == "cf-connecting-ip":
            edge_address = value.strip()
            if edge_address:
                return edge_address
    return socket_host or "unknown"


class SlidingWindowRateLimiter:
    """Allow at most ``limit`` events per ``window_seconds`` for each key."""

    def __init__(
        self,
        *,
        limit: int,
        window_seconds: int,
        max_clients: int = 2048,
    ) -> None:
        if limit < 1:
            raise ValueError("limit must be at least 1")
        if window_seconds < 1:
            raise ValueError("window_seconds must be at least 1")
        self._limit = limit
        self._window = float(window_seconds)
        self._max_clients = max_clients
        self._buckets: dict[str, deque[float]] = {}

    def check(self, key: str) -> tuple[bool, int]:
        """Record an event for ``key``.

        Returns ``(allowed, retry_after_seconds)``. ``retry_after`` is 0 when
        the request is allowed.
        """

        now = time.monotonic()
        cutoff = now - self._window
        bucket = self._buckets.get(key)
        if bucket is None:
            bucket = deque()
            self._buckets[key] = bucket

        while bucket and bucket[0] <= cutoff:
            bucket.popleft()

        if len(bucket) >= self._limit:
            retry_after = max(1, int(self._window - (now - bucket[0])) + 1)
            return False, retry_after

        bucket.append(now)
        self._prune(now)
        return True, 0

    def _prune(self, now: float) -> None:
        if len(self._buckets) <= self._max_clients:
            return
        cutoff = now - self._window
        stale = [key for key, bucket in self._buckets.items() if not bucket or bucket[-1] <= cutoff]
        for key in stale:
            del self._buckets[key]


__all__ = ["SlidingWindowRateLimiter", "resolve_client_key"]
