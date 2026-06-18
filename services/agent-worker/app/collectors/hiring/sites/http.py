"""
http.py
hiring 크롤러 공용 HTTP fetch 헬퍼 — retry + 지수 백오프(+지터) + timeout
+ UA 로테이션 + 429/403 적응형 백오프.

requests 경로(samsung/naver/sk_hynix/simple_sites)가 공유한다. 재시도 대상:
timeout·커넥션오류·5xx(지수 백오프), 그리고 429/403(적응형 — 429는 Retry-After 존중).
그 외 4xx는 즉시 raise. 매 시도마다 UA를 풀에서 로테이션해 차단 후 핑거프린트를 바꾼다.
timeout/재시도/백오프/UA 풀/캡은 Settings(HIRING_* env)로 제어한다.

스레드 안전성: hiring 수집 경로는 순차 실행(기업 1개씩, 단일 Selenium 드라이버
로테이션, rate-limit)이라 모듈 레벨 Session 싱글턴이 안전하다. 향후 동시(멀티스레드)
크롤을 도입하면 Session을 threading.local로 관리하도록 전환한다.
"""

from __future__ import annotations

import logging
import random
import time

import requests

from app.core.config import Settings, get_settings

from ..user_agents import pick_ua

logger = logging.getLogger(__name__)

_session: requests.Session | None = None

# ── 차단 신호 센서(#162 재검토 트리거 계측) ──────────────────────────────────
# requests 경로에서 관측된 403/429 발생 횟수를 런 단위로 집계한다. 프록시 인프라
# (#162)는 "실제 IP 차단이 측정될 때" 착수하기로 보류했으므로, 그 트리거를 자동으로
# 감지하는 가벼운 센서다. Selenium 경로 차단(블록 페이지)은 신호가 모호해 제외.
# NOTE: 현재 hiring 크롤러는 순차 실행 → 단순 모듈 카운터로 충분(_session 싱글턴과
# 동일 가정). 향후 asyncio/멀티스레드 도입 시 ContextVar 등으로 전환 필요(레이스 방지).
_BLOCK_SIGNALS: dict[str, int] = {"403": 0, "429": 0}


def record_block_signal(code: int) -> None:
    """403/429 관측 1건 기록(그 외 코드는 무시)."""
    key = str(code)
    if key in _BLOCK_SIGNALS:
        _BLOCK_SIGNALS[key] += 1


def block_signal_snapshot() -> dict[str, int]:
    """현재까지 집계된 차단 신호 카운트의 사본."""
    return dict(_BLOCK_SIGNALS)


def reset_block_signals() -> None:
    """런 시작 시 호출 — 크로스-런 누적(고스트) 방지."""
    for key in _BLOCK_SIGNALS:
        _BLOCK_SIGNALS[key] = 0


def _get_session() -> requests.Session:
    """커넥션 풀 재사용을 위한 지연 초기화 Session 싱글턴."""
    global _session
    if _session is None:
        _session = requests.Session()
    return _session


def _retry_after_seconds(response: requests.Response) -> float | None:
    """429의 Retry-After(정수 초)를 가드와 함께 파싱. 비정수/날짜형/없음 → None.

    음수는 0으로 클램프. 상한(cap)은 호출부에서 적용한다.
    """
    raw = response.headers.get("Retry-After")  # CaseInsensitiveDict
    if raw is None:
        return None
    try:
        return float(max(0, int(raw.strip())))
    except (TypeError, ValueError):
        return None  # HTTP-date 형식 등은 무시 → 지수 백오프로 폴백


def _retry_delay(exc: requests.RequestException, attempt: int, cfg: Settings) -> float | None:
    """재시도 대기 시간(초)을 반환. 재시도 불가면 None.

    - Timeout/ConnectionError/5xx → 지수 백오프(+지터).
    - 429 → Retry-After 존중(없으면 지수 백오프), cap 적용.
    - 403 → 지수 백오프(+UA 로테이션은 get 루프가 매 시도 수행), cap 적용.
    - 그 외 4xx → None(즉시 raise).
    """
    backoff = cfg.hiring_retry_backoff_seconds
    expo = backoff * (2 ** attempt) + random.uniform(0, backoff * 0.1)

    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return expo
    response = getattr(exc, "response", None)
    if response is None:
        return None
    code = response.status_code
    if 500 <= code < 600:
        return expo
    if code in (429, 403):
        retry_after = _retry_after_seconds(response) if code == 429 else None
        delay = retry_after if retry_after is not None else expo
        return min(delay, cfg.hiring_rate_limit_max_backoff_seconds)
    return None


def get(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    params: dict | None = None,
    settings: Settings | None = None,
) -> requests.Response:
    """retry/backoff + UA 로테이션이 적용된 GET. 성공 시 raise_for_status 통과 Response 반환.

    매 시도마다 UA를 풀에서 새로 주입(호출부 고정 UA를 덮어씀 — 로테이션이 목적).
    재시도 한도를 모두 소진하면 마지막 예외를 raise한다(호출부의 기존 except 동작 보존).
    """
    cfg = settings or get_settings()
    retries = max(0, cfg.hiring_max_retries)
    last_exc: requests.RequestException | None = None

    for attempt in range(retries + 1):
        # 매 시도 UA 로테이션 — 차단(403/429) 후 핑거프린트를 바꿔 재시도.
        attempt_headers = {**(headers or {}), "User-Agent": pick_ua(cfg)}
        try:
            response = _get_session().get(
                url, headers=attempt_headers, params=params,
                timeout=cfg.hiring_timeout_seconds,
            )
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            last_exc = exc
            # 차단 신호 센서: 403/429 응답을 시도 단위로 집계(#162 트리거 계측).
            resp = getattr(exc, "response", None)
            if resp is not None and resp.status_code in (403, 429):
                record_block_signal(resp.status_code)
            sleep_s = _retry_delay(exc, attempt, cfg)
            if attempt >= retries or sleep_s is None:
                raise
            logger.warning(
                "hiring fetch 재시도 %d/%d (%.2fs 후): %s — %s",
                attempt + 1, retries, sleep_s, url, exc,
            )
            time.sleep(sleep_s)

    # 도달 불가(루프가 return 또는 raise로 종료) — 방어적.
    raise last_exc  # type: ignore[misc]
