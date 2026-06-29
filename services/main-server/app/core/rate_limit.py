"""의존성 없는 인메모리 레이트리밋 + 로그인 잠금(브루트포스 방어).

⚠️ 단일 프로세스 기준이다(멀티워커·재시작 간 공유되지 않음). 학원/팀 프로젝트엔 충분하지만
운영 다중 인스턴스로 가면 Redis 등 공유 저장소 기반으로 교체해야 한다.
시간 의존이라 단위테스트는 임계값을 직접 낮춰(작은 max/window) 검증한다.
"""
from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from threading import Lock

from starlette.types import ASGIApp, Receive, Scope, Send


class FixedWindowLimiter:
    """슬라이딩 고정창 카운터. key 당 window 초 내 max 회까지 허용."""

    def __init__(self, max_requests: int, window_seconds: int) -> None:
        self.max = max_requests
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = Lock()

    def allow(self, key: str) -> bool:
        now = time.time()
        with self._lock:
            hits = self._hits[key]
            cutoff = now - self.window
            while hits and hits[0] < cutoff:
                hits.popleft()
            if len(hits) >= self.max:
                return False
            hits.append(now)
            return True

    def reset(self, key: str | None = None) -> None:
        with self._lock:
            if key is None:
                self._hits.clear()
            else:
                self._hits.pop(key, None)


class LoginLockout:
    """연속 실패 max_failures 회 → lock_seconds 동안 잠금. 성공 시 reset."""

    def __init__(self, max_failures: int, lock_seconds: int) -> None:
        self.max = max_failures
        self.lock_seconds = lock_seconds
        self._state: dict[str, list[float]] = {}  # key -> [fail_count, locked_until]
        self._lock = Lock()

    def retry_after(self, key: str) -> int:
        """잠금 중이면 남은 초(>0), 아니면 0."""
        now = time.time()
        with self._lock:
            rec = self._state.get(key)
            if rec and rec[1] > now:
                return int(rec[1] - now) + 1
            return 0

    def record_failure(self, key: str) -> None:
        now = time.time()
        with self._lock:
            rec = self._state.get(key) or [0.0, 0.0]
            rec[0] += 1
            if rec[0] >= self.max:
                rec[1] = now + self.lock_seconds
                rec[0] = 0.0
            self._state[key] = rec

    def reset(self, key: str) -> None:
        with self._lock:
            self._state.pop(key, None)


def client_ip_from_scope(scope: Scope) -> str:
    """프록시 뒤면 X-Forwarded-For 의 첫 IP, 아니면 소켓 peer."""
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            return value.decode("latin-1").split(",")[0].strip()
    client = scope.get("client")
    return client[0] if client else "unknown"


class RateLimitMiddleware:
    """지정 경로(인증 엔드포인트)의 POST 요청을 IP 기준으로 레이트리밋."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: FixedWindowLimiter,
        enabled: bool,
        path_prefixes: tuple[str, ...],
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.enabled = enabled
        self.path_prefixes = path_prefixes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not self.enabled or scope["method"] != "POST":
            await self.app(scope, receive, send)
            return
        path = scope["path"]
        if any(path.startswith(prefix) for prefix in self.path_prefixes):
            key = f"{client_ip_from_scope(scope)}:{path}"
            if not self.limiter.allow(key):
                await self._reject(send)
                return
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(send: Send) -> None:
        body = json.dumps(
            {"detail": {"code": "RATE_LIMITED", "message": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."}}
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 429,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"retry-after", b"60"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
