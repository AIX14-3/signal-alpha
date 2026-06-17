"""L2 지분·내부자 수집 — OpenDART ``majorstock``(대량보유 상황보고) + ``elestock``(임원·주요주주 소유보고).

corp_code 단위로 두 엔드포인트를 호출해, ``dart_ownership_events`` 적재용 통합 ownership-event
dict 리스트로 파싱한다. HTTP/재시도/에러 분류는 ``DartFinancialsClient``/``DartDisclosureClient`` 와
동일 패턴(urllib + asyncio.to_thread + 지수 백오프)을 따르고 ``DartApiError`` 를 재사용한다.

두 소스는 단일 테이블에 ``holder_type`` 으로 구분 적재한다:
- majorstock → ``major`` (5%+ 대량보유)
- elestock  → ``executive`` / ``main_shareholder`` (직위 표기로 구분)
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from datetime import date, datetime
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

MAJORSTOCK_PATH = "majorstock.json"
ELESTOCK_PATH = "elestock.json"

# OpenDART 호출 제한 대비 기본 요청 간격(초). L1/SEC fair-access(0.2)와 동일 보수값.
_DEFAULT_MIN_REQUEST_INTERVAL_SEC = 0.2


class DartOwnershipClient:
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

    def build_url(self, *, path: str, corp_code: str) -> str:
        query = urlencode({"crtfc_key": self._api_key, "corp_code": corp_code})
        return f"{self._base_url}/{path}?{query}"

    async def fetch_majorstock(self, *, corp_code: str) -> dict[str, Any]:
        return await self._fetch(path=MAJORSTOCK_PATH, corp_code=corp_code)

    async def fetch_elestock(self, *, corp_code: str) -> dict[str, Any]:
        return await self._fetch(path=ELESTOCK_PATH, corp_code=corp_code)

    async def _fetch(self, *, path: str, corp_code: str) -> dict[str, Any]:
        url = self.build_url(path=path, corp_code=corp_code)

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


class DartOwnershipCollector:
    source = "DART_OWNERSHIP"

    def __init__(
        self,
        *,
        api_key: str,
        corp_code_repository: CorpCodeRepository,
        client: DartOwnershipClient | None = None,
        min_request_interval_sec: float = _DEFAULT_MIN_REQUEST_INTERVAL_SEC,
    ) -> None:
        if not api_key:
            raise DartApiError("DART API key is required.")
        self._corp_code_repository = corp_code_repository
        self._client = client or DartOwnershipClient(
            api_key=api_key, min_request_interval_sec=min_request_interval_sec
        )

    async def collect(self, *, stock_code: str) -> list[dict[str, Any]]:
        ticker = stock_code.strip()
        corp_row = await self._corp_code_repository.get_corp_code_by_ticker(ticker)
        if corp_row is None:
            raise DartApiError(f"DART corp_code is not mapped for ticker: {ticker}")
        corp_code = corp_row["corp_code"]

        events: list[dict[str, Any]] = []
        major = await self._client.fetch_majorstock(corp_code=corp_code)
        for item in _status_rows(major):
            events.append(_majorstock_to_event(item, corp_code=corp_code))
        ele = await self._client.fetch_elestock(corp_code=corp_code)
        for item in _status_rows(ele):
            events.append(_elestock_to_event(item, corp_code=corp_code))
        # 한 보고서에서 같은 보고자가 여러 행으로 나뉘면 자연키가 충돌하므로 행 일련번호를 부여한다.
        _assign_line_seq(events)
        # 파싱 불가 행(rcept_no/보고자/날짜 누락)은 여기서 버리지 않는다 — 필수키가 빈 채로
        # 통과시켜 리포지토리 upsert 필터가 폐기하고 sync 의 skipped_count 로 가시화되게 한다.
        return events


def _status_rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    """status 검사 후 list 반환. 013(무자료)은 빈 리스트, 000 외엔 에러."""
    status = response.get("status")
    if status == "013":  # 조회된 데이터 없음
        return []
    if status != "000":
        raise DartApiError.from_status(status, response.get("message", ""))
    return list(response.get("list", []))


def _assign_line_seq(events: list[dict[str, Any]]) -> None:
    """(rcept_no, holder_name, holder_type) 그룹 내 응답 순서대로 line_seq 0,1,2…를 부여한다.
    보고서는 불변이라 재수집 시 순서가 안정적이어서 멱등성이 유지된다."""
    seen: dict[tuple[str, str, str], int] = defaultdict(int)
    for event in events:
        key = (event["rcept_no"], event["holder_name"], event["holder_type"])
        event["line_seq"] = seen[key]
        seen[key] += 1


def _majorstock_to_event(item: dict[str, Any], *, corp_code: str) -> dict[str, Any]:
    return {
        "corp_code": corp_code,
        "rcept_no": str(item.get("rcept_no") or "").strip(),
        "report_date": _to_date(item.get("rcept_dt")),
        "holder_name": str(item.get("repror") or "").strip(),
        "holder_type": "major",
        "shares": _to_int(item.get("stkqy")),
        "ratio": _to_ratio(item.get("stkrt")),
        "shares_delta": _to_int(item.get("stkqy_irds")),
        "ratio_delta": _to_ratio(item.get("stkrt_irds")),
        "report_reason": _clean(item.get("report_tp")),
    }


def _elestock_to_event(item: dict[str, Any], *, corp_code: str) -> dict[str, Any]:
    # 필드명은 OpenDART 개발가이드(DS004/2019022)로 검증됨. 분류는 직위(isu_exctv_ofcps)·
    # 주요주주 관계(isu_main_shrholdr) 전용 필드로 판정한다(문자열 휴리스틱 아님 — 리뷰 #4).
    ofcps = _clean(item.get("isu_exctv_ofcps"))
    main_shrholdr = _clean(item.get("isu_main_shrholdr"))
    return {
        "corp_code": corp_code,
        "rcept_no": str(item.get("rcept_no") or "").strip(),
        "report_date": _to_date(item.get("rcept_dt")),
        "holder_name": str(item.get("repror") or "").strip(),
        "holder_type": _elestock_holder_type(ofcps, main_shrholdr),
        "shares": _to_int(item.get("sp_stock_lmp_cnt")),
        "ratio": _to_ratio(item.get("sp_stock_lmp_rate")),
        "shares_delta": _to_int(item.get("sp_stock_lmp_irds_cnt")),
        "ratio_delta": _to_ratio(item.get("sp_stock_lmp_irds_rate")),
        "report_reason": ofcps or main_shrholdr,
    }


def _elestock_holder_type(ofcps: str | None, main_shrholdr: str | None) -> str:
    """주요주주 관계(isu_main_shrholdr, 예 '10%이상주주')가 명시되면 main_shareholder 로 우선
    분류한다. elestock 은 거의 모든 행에 직위(isu_exctv_ofcps)가 채워져 있어, 직위를 우선하면
    임원-주요주주 겸직 행이 전부 executive 로 흡수돼 희소·고신호인 주요주주를 잃는다(실데이터
    확인: 삼성 2618행 중 주요주주 6행이 모두 직위 보유). 직위만 있으면 executive, 둘 다
    없으면 기본 executive. 인자는 _clean 을 거쳐 빈값/'-' 가 None 으로 정규화돼 들어온다."""
    if main_shrholdr:
        return "main_shareholder"
    if ofcps:
        return "executive"
    return "executive"


def _clean(value: Any) -> str | None:
    """문자열 정규화. 빈값/'-'(DART 빈값 자리표시자) → None."""
    if value is None:
        return None
    text = str(value).strip()
    if text in ("", "-"):
        return None
    return text


def _to_int(value: Any) -> int | None:
    """주식수 문자열(쉼표 포함)을 정수로. 빈값/'-' → None."""
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


def _to_ratio(value: Any) -> float | None:
    """보유비율(%) 문자열을 실수로. 빈값/'-' → None."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "").replace("%", "")
    if text in ("", "-"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _to_date(value: Any) -> date | None:
    """rcept_dt(YYYYMMDD 또는 YYYY-MM-DD/YYYY.MM.DD)를 date로. 파싱 실패 → None."""
    if value is None:
        return None
    text = str(value).strip().replace("-", "").replace(".", "").replace("/", "")
    if len(text) != 8 or not text.isdigit():
        return None
    try:
        return datetime.strptime(text, "%Y%m%d").date()
    except ValueError:
        return None
