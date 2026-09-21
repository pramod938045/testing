"""In-memory conversation store shared by the web app and the Slack bot.

One `JiraChatAgent` per session key — a browser session id, or a Slack
channel+thread. Sessions expire so a long-running process doesn't accumulate
conversation history forever.
"""

from __future__ import annotations

import time
from typing import Callable, Generic, TypeVar

T = TypeVar("T")

DEFAULT_TTL_SECONDS = 60 * 60 * 4
DEFAULT_MAX_SESSIONS = 200


class SessionStore(Generic[T]):
    def __init__(
        self,
        factory: Callable[[], T],
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
    ):
        self._factory = factory
        self._ttl = ttl_seconds
        self._max = max_sessions
        self._items: dict[str, tuple[float, T]] = {}

    def __len__(self) -> int:
        return len(self._items)

    def __contains__(self, key: str) -> bool:
        return key in self._items

    def get(self, key: str) -> T:
        """Return the session for `key`, creating it on first use."""
        self._expire()
        item = self._items[key][1] if key in self._items else self._factory()
        self._items[key] = (time.time(), item)
        # Evict *after* inserting, or the cap lets the store grow past its limit.
        self._evict()
        return item

    def pop(self, key: str) -> T | None:
        entry = self._items.pop(key, None)
        return entry[1] if entry else None

    def prune(self) -> None:
        self._expire()
        self._evict()

    def _expire(self) -> None:
        now = time.time()
        for key in [k for k, (seen, _) in self._items.items() if now - seen > self._ttl]:
            self._items.pop(key, None)

    def _evict(self) -> None:
        while len(self._items) > self._max:
            oldest = min(self._items, key=lambda k: self._items[k][0])
            self._items.pop(oldest, None)
