"""
user_agents.py
hiring 크롤러 UA 로테이션 단일 소스.

requests 경로(sites/http.py)와 Selenium(driver_utils.py)이 같은 풀에서 UA를 뽑아
요청 핑거프린트를 분산한다(차단 우회). 풀은 데스크톱 전용이어야 한다 —
모바일 UA가 섞이면 m.* 모바일 레이아웃을 받아 파싱(3c)이 깨질 수 있다.

로테이션 시점 차이(주의):
  - requests: sites/http.py가 매 시도(attempt)마다 pick_ua()를 호출 → 재시도 시 즉시 UA 교체.
  - Selenium: 드라이버 생성 시점에만 UA 고정 → _safe_get 재시도는 동일 UA로 백오프만.
    (드라이버 로테이션 주기마다 새 UA로 갱신)
"""

from __future__ import annotations

import random

from app.core.config import Settings

# 풀이 비었을 때의 최후 폴백(설정 기본값이 있으므로 거의 쓰이지 않음).
_FALLBACK_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def pick_ua(settings: Settings | None = None) -> str:
    """설정된 데스크톱 UA 풀에서 무작위 1개 선택."""
    from app.core.config import get_settings

    cfg = settings or get_settings()
    pool = cfg.hiring_ua_pool or [_FALLBACK_UA]
    return random.choice(pool)
