"""
Claude API rate limiters — separate hourly buckets:
- BTC scanner uses claude_limiter_btc (btc_claude_max_calls_per_hour)
- Gold scanner + trade-manager reviews share claude_limiter_shared (claude_shared_max_calls_per_hour)
"""

import asyncio
import datetime
from collections import deque

from config.settings import settings


class ClaudeRateLimiter:
    def __init__(self, max_calls: int = 20, window_seconds: int = 3600):
        self.max_calls = max_calls
        self.window = window_seconds
        self._calls: deque = deque()
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
        now = datetime.datetime.utcnow().timestamp()
        self._prune(now)
        return len(self._calls), self.max_calls


def _btc_max() -> int:
    return max(10, int(getattr(settings, "btc_claude_max_calls_per_hour", 120)))


def _shared_max() -> int:
    return max(5, int(getattr(settings, "claude_shared_max_calls_per_hour", 24)))


claude_limiter_btc = ClaudeRateLimiter(max_calls=_btc_max(), window_seconds=3600)
claude_limiter_shared = ClaudeRateLimiter(max_calls=_shared_max(), window_seconds=3600)

# Back-compat alias: shared pool (Gold + reviews)
claude_limiter = claude_limiter_shared
