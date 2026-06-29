"""인증 레이트리밋·관리자 로그인 잠금 싱글턴(설정값으로 1회 생성).

라우트(admin 로그인)와 미들웨어(인증 경로 레이트리밋)가 같은 인스턴스를 공유한다.
"""
from __future__ import annotations

from functools import lru_cache

from app.core.config import get_settings
from app.core.rate_limit import FixedWindowLimiter, LoginLockout


@lru_cache
def get_auth_rate_limiter() -> FixedWindowLimiter:
    settings = get_settings()
    return FixedWindowLimiter(
        settings.rate_limit_auth_max, settings.rate_limit_auth_window_seconds
    )


@lru_cache
def get_admin_login_lockout() -> LoginLockout:
    settings = get_settings()
    return LoginLockout(settings.admin_login_max_failures, settings.admin_lockout_seconds)
