"""Small in-process TTL + LRU cache.

This is deliberately dependency-free. It is suitable for a single process, but a
multi-instance deployment should replace it with Redis so cached analyses are shared
and memory use remains bounded across replicas.
"""

from __future__ import annotations

import asyncio
import copy
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Generic, TypeVar

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class CacheStats:
    entries: int
    hits: int
    misses: int


class TTLCache(Generic[T]):
    def __init__(self, ttl_seconds: int, max_entries: int) -> None:
        self._ttl = ttl_seconds
        self._max_entries = max_entries
        self._items: OrderedDict[str, tuple[float, T]] = OrderedDict()
        self._lock = asyncio.Lock()
        self._hits = 0
        self._misses = 0

    @property
    def enabled(self) -> bool:
        return self._ttl > 0 and self._max_entries > 0

    async def get(self, key: str) -> T | None:
        if not self.enabled:
            self._misses += 1
            return None

        async with self._lock:
            item = self._items.get(key)
            if item is None:
                self._misses += 1
                return None

            expires_at, value = item
            if expires_at <= time.monotonic():
                del self._items[key]
                self._misses += 1
                return None

            self._items.move_to_end(key)
            self._hits += 1
            return copy.deepcopy(value)

    async def set(self, key: str, value: T) -> None:
        if not self.enabled:
            return

        async with self._lock:
            now = time.monotonic()
            self._purge_expired(now)
            self._items[key] = (now + self._ttl, copy.deepcopy(value))
            self._items.move_to_end(key)
            while len(self._items) > self._max_entries:
                self._items.popitem(last=False)

    async def clear(self) -> None:
        async with self._lock:
            self._items.clear()

    async def stats(self) -> CacheStats:
        async with self._lock:
            self._purge_expired(time.monotonic())
            return CacheStats(
                entries=len(self._items),
                hits=self._hits,
                misses=self._misses,
            )

    def _purge_expired(self, now: float) -> None:
        expired = [key for key, (expires_at, _) in self._items.items() if expires_at <= now]
        for key in expired:
            del self._items[key]
