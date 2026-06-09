from __future__ import annotations

import asyncio
import json
from datetime import date, timedelta
from typing import Any, Protocol
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.schemas.evidence import RawEvidence


class DartApiError(RuntimeError):
    pass


class CorpCodeRepository(Protocol):
    async def get_corp_code_by_ticker(self, ticker: str) -> Any:
        pass


class DartDisclosureClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://opendart.fss.or.kr/api",
        timeout_seconds: int = 10,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def list_disclosures(
        self,
        *,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        page_no: int = 1,
        page_count: int = 100,
    ) -> dict[str, Any]:
        url = self.build_list_url(
            corp_code=corp_code,
            bgn_de=bgn_de,
            end_de=end_de,
            page_no=page_no,
            page_count=page_count,
        )
        return await asyncio.to_thread(self._get_json, url)

    def build_list_url(
        self,
        *,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        page_no: int,
        page_count: int,
    ) -> str:
        query = urlencode(
            {
                "crtfc_key": self._api_key,
                "corp_code": corp_code,
                "bgn_de": bgn_de,
                "end_de": end_de,
                "page_no": str(page_no),
                "page_count": str(page_count),
            }
        )
        return f"{self._base_url}/list.json?{query}"

    def _get_json(self, url: str) -> dict[str, Any]:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=self._timeout_seconds) as response:
            body = response.read().decode("utf-8")
        return json.loads(body)


class DartCollector:
    source = "DART"

    def __init__(
        self,
        *,
        api_key: str,
        corp_code_repository: CorpCodeRepository,
        client: DartDisclosureClient | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        page_size: int = 100,
    ) -> None:
        if not api_key:
            raise DartApiError("DART API key is required.")

        self._api_key = api_key
        self._corp_code_repository = corp_code_repository
        self._client = client or DartDisclosureClient(api_key=api_key)
        self._start_date = start_date
        self._end_date = end_date
        self._page_size = page_size

    async def collect(self, stock_code: str) -> list[RawEvidence]:
        ticker = stock_code.strip()
        corp_row = await self._corp_code_repository.get_corp_code_by_ticker(ticker)
        if corp_row is None:
            raise DartApiError(f"DART corp_code is not mapped for ticker: {ticker}")

        corp_code = corp_row["corp_code"]
        bgn_de = self._start_date or _default_start_date()
        end_de = self._end_date or _default_end_date()
        first_response = await self._fetch_page(
            corp_code=corp_code,
            bgn_de=bgn_de,
            end_de=end_de,
            page_no=1,
        )
        if first_response is None:
            return []

        disclosures = list(first_response.get("list", []))
        total_page = _int_response_value(first_response.get("total_page"), default=1)
        for page_no in range(2, total_page + 1):
            response = await self._fetch_page(
                corp_code=corp_code,
                bgn_de=bgn_de,
                end_de=end_de,
                page_no=page_no,
            )
            if response is None:
                break
            disclosures.extend(response.get("list", []))

        return [
            _disclosure_to_evidence(ticker, corp_code, item)
            for item in disclosures
        ]

    async def _fetch_page(
        self,
        *,
        corp_code: str,
        bgn_de: str,
        end_de: str,
        page_no: int,
    ) -> dict[str, Any] | None:
        response = await self._client.list_disclosures(
            corp_code=corp_code,
            bgn_de=bgn_de,
            end_de=end_de,
            page_no=page_no,
            page_count=self._page_size,
        )
        status = response.get("status")
        if status == "013":
            return None
        if status != "000":
            raise DartApiError(f"DART API failed: {status} {response.get('message', '')}".strip())
        return response


def _disclosure_to_evidence(
    stock_code: str,
    corp_code: str,
    item: dict[str, Any],
) -> RawEvidence:
    receipt_no = item["rcept_no"]
    report_name = item.get("report_nm", "")
    receipt_date = _format_receipt_date(item.get("rcept_dt"))
    return RawEvidence(
        source="DART",
        stock_code=stock_code,
        title=report_name,
        content=report_name,
        published_at=receipt_date,
        url=f"https://dart.fss.or.kr/dsaf001/main.do?rcpNo={receipt_no}",
        metadata={
            "receipt_no": receipt_no,
            "external_id": receipt_no,
            "corp_code": item.get("corp_code", corp_code),
            "corp_name": item.get("corp_name"),
            "stock_code": item.get("stock_code", stock_code),
            "corp_cls": item.get("corp_cls"),
            "report_name": report_name,
            "disclosure_type": _infer_disclosure_type(report_name),
            "priority": "batch",
            "published_at": receipt_date,
            "source_name": "OpenDART",
            "filer_name": item.get("flr_nm"),
            "remark": item.get("rm"),
            "raw_payload": item,
        },
    )


def _format_receipt_date(value: str | None) -> str | None:
    if not value:
        return None
    if len(value) == 8:
        return f"{value[0:4]}-{value[4:6]}-{value[6:8]}"
    return value


def _infer_disclosure_type(report_name: str) -> str | None:
    if "사업보고서" in report_name:
        return "annual_report"
    if "반기보고서" in report_name:
        return "half_report"
    if "분기보고서" in report_name:
        return "quarter_report"
    if "주요사항보고서" in report_name:
        return "material_event"
    return None


def _int_response_value(value: Any, *, default: int) -> int:
    if value in (None, ""):
        return default
    return int(value)


def _default_start_date() -> str:
    return (date.today() - timedelta(days=30)).strftime("%Y%m%d")


def _default_end_date() -> str:
    return date.today().strftime("%Y%m%d")
