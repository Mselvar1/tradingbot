"""
Shared Claude API rate limiter — sliding window, max N calls/hour (configurable)
across scanners and trade-manager reviews.
"""

import asyncio
import datetime
from collections import deque

from config.settings import settings


class ClaudeRateLimiter:
    def __init__(self, max_calls: int = 20, window_seconds: int = 3600):
        self.max_calls = max_calls
        self.window = window_seconds
        self._calls: deque = deque()   # timestamps of recent calls
        self._lock: asyncio.Lock | None = None

    @property
    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    def _prune(self, now: float):
        cutoff = now - self.window
        while self._calls and self._calls[0] < cutoff:
            self._calls.popleft()

    async def acquire(self, scanner: str = "") -> bool:
        """
        Returns True and records the call if within the rate limit.
        Returns False (and logs) if the hourly cap has been reached.
        """
        async with self._get_lock:
            now = datetime.datetime.utcnow().timestamp()
            self._prune(now)
            used = len(self._calls)
            if used >= self.max_calls:
                reset_in = int(self._calls[0] + self.window - now)
                print(
                    f"Rate limit: {used}/{self.max_calls} Claude calls this hour — "
                    f"{scanner} skipped (cap resets in {reset_in}s)"
                )
                return False
            self._calls.append(now)
            print(f"Rate limit: {used + 1}/{self.max_calls} Claude calls this hour ({scanner})")
            return True

    def usage(self) -> tuple[int, int]:
        """Returns (calls_used, max_calls) for the current window."""
        now = datetime.datetime.utcnow().timestamp()
        self._prune(now)
        return len(self._calls), self.max_calls


def _limiter_max() -> int:
    return max(20, int(getattr(settings, "claude_max_calls_per_hour", 120)))


# Singleton — max calls/hour from settings (default 120)
claude_limiter = ClaudeRateLimiter(max_calls=_limiter_max(), window_seconds=3600)
