"""
http.py
hiring 크롤러 공용 HTTP fetch 헬퍼 — 요청 단위 retry + 지수 백오프(+지터) + timeout.

requests 경로(samsung/naver/sk_hynix/simple_sites)가 공유한다. 일시적 실패
(timeout·커넥션오류·5xx)만 재시도하고 4xx는 즉시 raise한다(429/403 적응형 처리는
후속 #146에서 추가). timeout/재시도/백오프는 Settings(HIRING_* env)로 제어한다.

스레드 안전성: hiring 수집 경로는 순차 실행(기업 1개씩, 단일 Selenium 드라이버
로테이션, rate-limit)이라 모듈 레벨 Session 싱글턴이 안전하다. 향후 동시(멀티스레드)
크롤을 도입하면 Session을 threading.local로 관리하도록 전환한다
(SecFilingsClient가 인스턴스당 httpx.Client를 보유하는 것과 동일 취지).
"""

from __future__ import annotations

import logging
import random
import time

import requests

from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)

_session: requests.Session | None = None


def _get_session() -> requests.Session:
    """커넥션 풀 재사용을 위한 지연 초기화 Session 싱글턴."""
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def _is_retryable(exc: requests.RequestException) -> bool:
    """일시적 실패(재시도 가치 있음)인지 판정.

    - Timeout / ConnectionError → 재시도.
    - HTTPError → 5xx만 재시도(4xx는 비재시도; 429/403 특별 처리는 #146).
    """
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    response = getattr(exc, "response", None)
    return response is not None and 500 <= response.status_code < 600


def get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict | None = None,
    settings: Settings | None = None,
) -> requests.Response:
    """retry/backoff가 적용된 GET. 성공 시 raise_for_status를 통과한 Response 반환.

    재시도 한도를 모두 소진하면 마지막 예외를 raise한다(호출부의 기존 except 동작 보존).
    """
    cfg = settings or get_settings()
    retries = max(0, cfg.hiring_max_retries)
    backoff = cfg.hiring_retry_backoff_seconds
    last_exc: requests.RequestException | None = None

    for attempt in range(retries + 1):
        try:
            response = _get_session().get(
                url, headers=headers, params=params, timeout=cfg.hiring_timeout_seconds
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            if attempt >= retries or not _is_retryable(exc):
                raise
            # 지수 백오프 + 지터(thundering-herd 완화).
            sleep_s = backoff * (2 ** attempt) + random.uniform(0, backoff * 0.1)
            logger.warning(
                "hiring fetch 재시도 %d/%d (%.2fs 후): %s — %s",
                attempt + 1, retries, sleep_s, url, exc,
            )
            time.sleep(sleep_s)

    # 도달 불가(루프가 return 또는 raise로 종료) — 방어적.
    raise last_exc  # type: ignore[misc]
