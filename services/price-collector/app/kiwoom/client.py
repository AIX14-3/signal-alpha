"""Kiwoom OpenAPI+ client abstraction.

The real implementation wraps ``pykiwoom`` which depends on the Windows-only
COM control, so it is imported lazily. Collectors depend only on the
``KiwoomClient`` protocol, which keeps them testable with a fake on any OS.
"""

import time
from collections import deque
from typing import Protocol


class KiwoomClient(Protocol):
    def request(
        self,
        tr_code: str,
        output: str,
        **inputs: str
    ) -> list[dict[str, str]]:
        """Run a single TR request and return rows as raw string field maps."""


class RateLimiter:
    """Enforce Kiwoom's TR limits: a minimum gap plus a per-minute ceiling."""

    def __init__(
        self,
        min_interval_sec: float = 0.2,
        max_per_minute: int = 100,
        sleep=time.sleep,
        monotonic=time.monotonic
    ) -> None:
        self._min_interval_sec = min_interval_sec
        self._max_per_minute = max_per_minute
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_call: float | None = None
        self._calls: deque[float] = deque()

    def wait(self) -> None:
        now = self._monotonic()
        if self._last_call is not None:
            gap = now - self._last_call
            if gap < self._min_interval_sec:
                self._sleep(self._min_interval_sec - gap)

        now = self._monotonic()
        while self._calls and now - self._calls[0] >= 60.0:
            self._calls.popleft()
        if len(self._calls) >= self._max_per_minute:
            self._sleep(60.0 - (now - self._calls[0]))
            now = self._monotonic()
            while self._calls and now - self._calls[0] >= 60.0:
                self._calls.popleft()

        self._last_call = now
        self._calls.append(now)


class PykiwoomClient:
    """Live client backed by ``pykiwoom`` (Windows / COM only)."""

    def __init__(self, limiter: RateLimiter | None = None) -> None:
        from pykiwoom.kiwoom import Kiwoom  # imported here: Windows-only dep

        self._kiwoom = Kiwoom()
        self._kiwoom.CommConnect(block=True)
        self._limiter = limiter or RateLimiter()

    def request(
        self,
        tr_code: str,
        output: str,
        **inputs: str
    ) -> list[dict[str, str]]:
        self._limiter.wait()
        next_value = int(inputs.pop("next", "0") or 0)
        frame = self._kiwoom.block_request(
            tr_code,
            output=output,
            next=next_value,
            **inputs
        )
        if frame is None:
            return []
        records = frame.to_dict("records")
        return [{str(k): str(v) for k, v in row.items()} for row in records]
