"""L3 임직원 현황 수집 — OpenDART ``empSttus``(직원 현황, 정기보고서).

corp_code × bsns_year × reprt_code 단위로 호출해, ``dart_employee_stats`` 적재용
employee-stat dict 리스트로 파싱한다. HTTP/재시도/에러 분류는 ``DartFinancialsClient`` /
``DartOwnershipClient`` 와 동일 패턴(urllib + asyncio.to_thread + 지수 백오프)을 따르고
``DartApiError`` 를 재사용한다.

라이브 검증(삼성 00126380, 2024 사업보고서)으로 확정된 행 구조:
- 사업부문(fo_bbm) × 성별(sexdstn) 로 행이 쪼개진다(전사합계 행 포함).
- 1인평균급여(jan_salary_am)·연간급여총액(fyer_salary_totamt)은 전사합계 행에만 채워지고
  사업부문별 행은 '-'(빈값) 이다 → 여기서 None 으로 정규화한다.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections import defaultdict
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# 형제 모듈(collectors/dart)의 에러 타입·재시도 분류 재사용 — DART 수집 전반 일관성 유지.
from app.collectors.dart.disclosure import (
    DartApiError,
    _retryable_status_error,
    _to_dart_error,
)
from app.collectors.price.rate_limiter import RateLimiter

EMPLOYEE_PATH = "empSttus.json"

# OpenDART 호출 제한 대비 기본 요청 간격(초). L1/L2 fair-access(0.2)와 동일 보수값.
_DEFAULT_MIN_REQUEST_INTERVAL_SEC = 0.2


class DartEmployeeClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://opendart.fss.or.kr/api",
        timeout_seconds: int = 10,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
        min_request_interval_sec: float = 0.0,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds
        self._limiter = RateLimiter(min_request_interval_sec)

    def build_url(self, *, corp_code: str, bsns_year: int, reprt_code: str) -> str:
        query = urlencode(
            {
                "crtfc_key": self._api_key,
                "corp_code": corp_code,
                "bsns_year": str(bsns_year),
                "reprt_code": reprt_code,
            }
        )
        return f"{self._base_url}/{EMPLOYEE_PATH}?{query}"

    async def fetch_employees(
        self,
        *,
        corp_code: str,
        bsns_year: int,
        reprt_code: str,
    ) -> dict[str, Any]:
        url = self.build_url(corp_code=corp_code, bsns_year=bsns_year, reprt_code=reprt_code)

        async def request() -> dict[str, Any]:
            await self._limiter.wait()
            response = await asyncio.to_thread(self._get_json, url)
            # 본문 status가 재시도 가능(020 rate-limit, 800/900 서비스장애)이면
            # 재시도 루프가 받도록 다시 던진다. (DartFinancialsClient와 동일 패턴)
            retry_error = _retryable_status_error(response)
            if retry_error:
                raise retry_error
            return response

        return await self._with_retry(request)

    async def _with_retry(self, request: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await request()
            except Exception as exc:  # noqa: BLE001 - 분류 후 재던짐
                error = _to_dart_error(exc)
                last_error = error
                if not error.retryable or attempt >= self._max_retries:
                    raise error from exc
                await asyncio.sleep(self._retry_backoff_seconds * (2**attempt))
        raise last_error or DartApiError("DART API failed.")

    def _get_json(self, url: str) -> dict[str, Any]:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=self._timeout_seconds) as response:
            body = response.read().decode("utf-8")
        return json.loads(body)


class CorpCodeRepository(Protocol):
    async def get_corp_code_by_ticker(self, ticker: str) -> Any:
        pass


class DartEmployeeCollector:
    source = "DART_EMPLOYEE"

    def __init__(
        self,
        *,
        api_key: str,
        corp_code_repository: CorpCodeRepository,
        client: DartEmployeeClient | None = None,
        min_request_interval_sec: float = _DEFAULT_MIN_REQUEST_INTERVAL_SEC,
    ) -> None:
        if not api_key:
            raise DartApiError("DART API key is required.")
        self._corp_code_repository = corp_code_repository
        self._client = client or DartEmployeeClient(
            api_key=api_key, min_request_interval_sec=min_request_interval_sec
        )

    async def collect(
        self, *, stock_code: str, bsns_year: int, reprt_code: str
    ) -> list[dict[str, Any]]:
        ticker = stock_code.strip()
        corp_row = await self._corp_code_repository.get_corp_code_by_ticker(ticker)
        if corp_row is None:
            raise DartApiError(f"DART corp_code is not mapped for ticker: {ticker}")
        corp_code = corp_row["corp_code"]

        response = await self._client.fetch_employees(
            corp_code=corp_code, bsns_year=bsns_year, reprt_code=reprt_code
        )
        stats: list[dict[str, Any]] = []
        for item in _status_rows(response):
            stats.append(
                _emp_row_to_stat(
                    item, corp_code=corp_code, bsns_year=bsns_year, reprt_code=reprt_code
                )
            )
        # 같은 (segment, sex) 가 한 보고서에서 여러 행으로 나오면 자연키가 충돌하므로 행 일련번호를 부여한다.
        _assign_line_seq(stats)
        # 파싱 불가 행(rcept_no 누락)은 여기서 버리지 않는다 — 필수키가 빈 채로 통과시켜
        # 리포지토리 upsert 필터가 폐기하고 sync 의 skipped_count 로 가시화되게 한다.
        return stats


def _status_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    """status 검사 후 list 반환. 013(무자료)은 빈 리스트, 000 외엔 에러."""
    status = response.get("status")
    if status == "013":  # 조회된 데이터 없음
        return []
    if status != "000":
        raise DartApiError.from_status(status, response.get("message", ""))
    return list(response.get("list", []))


def _assign_line_seq(stats: list[dict[str, Any]]) -> None:
    """(rcept_no, segment, sex) 그룹 내 응답 순서대로 line_seq 0,1,2…를 부여한다.
    보고서는 불변이라 재수집 시 순서가 안정적이어서 멱등성이 유지된다(L2 _assign_line_seq 동일)."""
    seen: dict[tuple[Any, Any, Any], int] = defaultdict(int)
    for stat in stats:
        key = (stat["rcept_no"], stat["segment"], stat["sex"])
        stat["line_seq"] = seen[key]
        seen[key] += 1


def _emp_row_to_stat(
    item: dict[str, Any], *, corp_code: str, bsns_year: int, reprt_code: str
) -> dict[str, Any]:
    return {
        "corp_code": corp_code,
        "rcept_no": str(item.get("rcept_no") or "").strip(),
        "bsns_year": bsns_year,
        "reprt_code": reprt_code,
        "segment": _clean(item.get("fo_bbm")),
        "sex": _clean(item.get("sexdstn")),
        "headcount": _to_int(item.get("sm")),
        "regular_count": _to_int(item.get("rgllbr_co")),
        "contract_count": _to_int(item.get("cnttk_co")),
        "avg_tenure_years": _to_ratio(item.get("avrg_cnwk_sdytrn")),
        # 급여 필드는 합계 행에만 채워지고 사업부문별 행은 '-' → None 정규화.
        "avg_salary_krw": _to_int(item.get("jan_salary_am")),
        "salary_total_krw": _to_int(item.get("fyer_salary_totamt")),
    }


def _clean(value: Any) -> str | None:
    """문자열 정규화. 빈값/'-'(DART 빈값 자리표시자) → None."""
    if value is None:
        return None
    text = str(value).strip()
    if text in ("", "-"):
        return None
    return text


def _to_int(value: Any) -> int | None:
    """인원/금액 문자열(쉼표 포함)을 정수로. 빈값/'-' → None."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in ("", "-"):
        return None
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except ValueError:
            return None


_LEADING_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


def _to_ratio(value: Any) -> float | None:
    """평균근속연수 등 실수 문자열을 float로. 빈값/'-' → None.
    비표준 공시는 단위 접미사를 붙이기도 하므로('16.9년', '약 16.9') 선두 숫자 토큰을 추출한다."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in ("", "-"):
        return None
    match = _LEADING_NUMBER_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group())
    except ValueError:
        return None
