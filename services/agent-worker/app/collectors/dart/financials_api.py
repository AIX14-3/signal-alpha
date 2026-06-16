"""L1 정형 재무 수집 — OpenDART ``fnlttSinglAcntAll``(단일회사 전체 재무제표).

한 번 호출로 BS/IS/CIS/CF/SCE 전 계정을 표준계정ID와 함께 받아, ``dart_financial_facts``
적재용 fact dict 리스트로 파싱한다. HTTP/재시도/에러 분류는 기존
``DartDisclosureClient`` 와 동일 패턴(urllib + asyncio.to_thread + 지수 백오프)을 따르고
``DartApiError`` 를 재사용한다.

당기(thstrm) 금액만 적재한다. 연속 시계열은 ``bsns_year`` 를 바꿔 다회 호출해 채운다
(spec §1) — 한 응답의 전기/전전기까지 적재하면 기간 키가 충돌·이중계상되기 때문이다.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# 형제 모듈(collectors/dart)의 에러 타입·매핑 재사용 — DART 수집 전반 일관성 유지.
from app.collectors.dart.disclosure import DartApiError, _to_dart_error

FINANCIALS_PATH = "fnlttSinglAcntAll.json"

# reprt_code → 기간 라벨 접미사 / 누적 여부.
_REPRT_SUFFIX = {"11013": "Q1", "11012": "H1", "11014": "Q3", "11011": "FY"}
_ANNUAL_REPRT = "11011"


class DartFinancialsClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://opendart.fss.or.kr/api",
        timeout_seconds: int = 10,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

    def build_url(self, *, corp_code: str, bsns_year: int, reprt_code: str, fs_div: str) -> str:
        query = urlencode(
            {
                "crtfc_key": self._api_key,
                "corp_code": corp_code,
                "bsns_year": str(bsns_year),
                "reprt_code": reprt_code,
                "fs_div": fs_div,
            }
        )
        return f"{self._base_url}/{FINANCIALS_PATH}?{query}"

    async def fetch_financials(
        self,
        *,
        corp_code: str,
        bsns_year: int,
        reprt_code: str,
        fs_div: str,
    ) -> dict[str, Any]:
        url = self.build_url(
            corp_code=corp_code,
            bsns_year=bsns_year,
            reprt_code=reprt_code,
            fs_div=fs_div,
        )
        return await self._with_retry(lambda: asyncio.to_thread(self._get_json, url))

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


class DartFinancialsCollector:
    source = "DART_FINANCIALS"

    def __init__(
        self,
        *,
        api_key: str,
        corp_code_repository: CorpCodeRepository,
        client: DartFinancialsClient | None = None,
        fs_priority: tuple[str, ...] = ("CFS", "OFS"),
    ) -> None:
        if not api_key:
            raise DartApiError("DART API key is required.")
        self._corp_code_repository = corp_code_repository
        self._client = client or DartFinancialsClient(api_key=api_key)
        self._fs_priority = fs_priority

    async def collect(
        self,
        *,
        stock_code: str,
        bsns_year: int,
        reprt_code: str,
    ) -> list[dict[str, Any]]:
        ticker = stock_code.strip()
        corp_row = await self._corp_code_repository.get_corp_code_by_ticker(ticker)
        if corp_row is None:
            raise DartApiError(f"DART corp_code is not mapped for ticker: {ticker}")
        corp_code = corp_row["corp_code"]

        # 연결(CFS) 우선, 빈값/무자료면 별도(OFS) 폴백.
        for fs_div in self._fs_priority:
            response = await self._client.fetch_financials(
                corp_code=corp_code,
                bsns_year=bsns_year,
                reprt_code=reprt_code,
                fs_div=fs_div,
            )
            status = response.get("status")
            if status == "013":  # 조회된 데이터 없음
                continue
            if status != "000":
                raise DartApiError.from_status(status, response.get("message", ""))
            rows = list(response.get("list", []))
            if not rows:
                continue
            return [
                _account_to_fact(
                    item,
                    corp_code=corp_code,
                    bsns_year=bsns_year,
                    reprt_code=reprt_code,
                    fs_div=fs_div,
                )
                for item in rows
            ]
        return []


def _account_to_fact(
    item: dict[str, Any],
    *,
    corp_code: str,
    bsns_year: int,
    reprt_code: str,
    fs_div: str,
) -> dict[str, Any]:
    account_id = (item.get("account_id") or "").strip() or None
    amount_raw = item.get("thstrm_amount")
    return {
        "corp_code": corp_code,
        "rcept_no": str(item.get("rcept_no") or "").strip(),
        "bsns_year": int(bsns_year),
        "reprt_code": reprt_code,
        "fs_div": fs_div,
        "sj_div": str(item.get("sj_div") or "").strip(),
        "account_id": account_id,
        "account_nm": str(item.get("account_nm") or "").strip(),
        "amount_krw": _to_krw(amount_raw),
        "amount_raw": amount_raw,
        "currency": str(item.get("currency") or "KRW").strip() or "KRW",
        "period_label": _period_label(bsns_year, reprt_code),
        "fiscal_period": _fiscal_period(reprt_code),
    }


def _to_krw(value: Any) -> int | None:
    """fnlttSinglAcntAll 금액 문자열(원 단위, 쉼표 포함)을 정수로. 빈값/'-' → None."""
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


def _period_label(bsns_year: int, reprt_code: str) -> str:
    return f"{int(bsns_year)}{_REPRT_SUFFIX.get(reprt_code, reprt_code)}"


def _fiscal_period(reprt_code: str) -> str:
    return "annual" if reprt_code == _ANNUAL_REPRT else "cumulative"
