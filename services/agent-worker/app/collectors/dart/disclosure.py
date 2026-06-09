from __future__ import annotations

import asyncio
import io
import json
import zipfile
from datetime import date, timedelta
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from app.schemas.evidence import RawEvidence


class DartApiError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status: str | None = None,
        category: str = "unknown",
        retryable: bool = False,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.category = category
        self.retryable = retryable

    @classmethod
    def from_status(cls, status: str | None, message: str | None = None) -> "DartApiError":
        resolved_status = status or "unknown"
        category, retryable = _classify_dart_status(resolved_status)
        detail = message or "DART API failed."
        return cls(
            f"DART API failed: {resolved_status} {detail}".strip(),
            status=resolved_status,
            category=category,
            retryable=retryable,
        )


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
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
    ) -> None:
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._retry_backoff_seconds = retry_backoff_seconds

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
        return await self._json_with_retry(url)

    async def fetch_document(self, receipt_no: str) -> dict[str, Any]:
        url = self.build_document_url(receipt_no)
        body = await self._bytes_with_retry(url)
        return self.parse_document_zip(body)

    async def _json_with_retry(self, url: str) -> dict[str, Any]:
        async def request() -> dict[str, Any]:
            response = await asyncio.to_thread(self._get_json, url)
            retry_error = _retryable_status_error(response)
            if retry_error:
                raise retry_error
            return response

        return await self._with_retry(request)

    async def _bytes_with_retry(self, url: str) -> bytes:
        async def request() -> bytes:
            return await asyncio.to_thread(self._get_bytes, url)

        return await self._with_retry(request)

    async def _with_retry(self, request: Any) -> Any:
        last_error: Exception | None = None
        for attempt in range(self._max_retries + 1):
            try:
                return await request()
            except Exception as exc:
                error = _to_dart_error(exc)
                last_error = error
                if not error.retryable or attempt >= self._max_retries:
                    raise error from exc
                await asyncio.sleep(self._retry_backoff_seconds * (2 ** attempt))
        raise last_error or DartApiError("DART API failed.")

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

    def build_document_url(self, receipt_no: str) -> str:
        query = urlencode(
            {
                "crtfc_key": self._api_key,
                "rcept_no": receipt_no,
            }
        )
        return f"{self._base_url}/document.xml?{query}"

    def _get_json(self, url: str) -> dict[str, Any]:
        request = Request(url, headers={"Accept": "application/json"})
        with urlopen(request, timeout=self._timeout_seconds) as response:
            body = response.read().decode("utf-8")
        return json.loads(body)

    def _get_bytes(self, url: str) -> bytes:
        request = Request(url, headers={"Accept": "application/zip, application/octet-stream"})
        with urlopen(request, timeout=self._timeout_seconds) as response:
            return response.read()

    @staticmethod
    def parse_document_zip(body: bytes) -> dict[str, Any]:
        try:
            with zipfile.ZipFile(io.BytesIO(body)) as archive:
                files = []
                document_texts = []
                for name in archive.namelist():
                    if name.endswith("/"):
                        continue
                    raw_text = _decode_document_bytes(archive.read(name))
                    text = _extract_xml_text(raw_text)
                    files.append({"name": name, "text_length": len(text)})
                    if text:
                        document_texts.append(text)
        except zipfile.BadZipFile as exc:
            raise DartApiError(
                "DART document response is not a ZIP file.",
                category="parse",
                retryable=False,
            ) from exc

        return {
            "text": "\n\n".join(document_texts).strip(),
            "files": files,
        }


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
        fetch_documents: bool = True,
    ) -> None:
        if not api_key:
            raise DartApiError("DART API key is required.")

        self._api_key = api_key
        self._corp_code_repository = corp_code_repository
        self._client = client or DartDisclosureClient(api_key=api_key)
        self._start_date = start_date
        self._end_date = end_date
        self._page_size = page_size
        self._fetch_documents = fetch_documents

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

        evidence = []
        for item in disclosures:
            document = await self._document_for_disclosure(item) if self._fetch_documents else None
            evidence.append(_disclosure_to_evidence(ticker, corp_code, item, document=document))
        return evidence

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
            raise DartApiError.from_status(status, response.get("message", ""))
        return response

    async def _document_for_disclosure(self, item: dict[str, Any]) -> dict[str, Any]:
        receipt_no = item["rcept_no"]
        try:
            document = await self._client.fetch_document(receipt_no)
        except DartApiError as exc:
            return {
                "document_fetch_status": "failed",
                "document_error": str(exc),
                "document_error_status": exc.status,
                "document_error_category": exc.category,
                "document_error_retryable": exc.retryable,
            }
        return {
            "document_fetch_status": "success",
            "document_text": document.get("text", ""),
            "document_files": document.get("files", []),
        }


def _disclosure_to_evidence(
    stock_code: str,
    corp_code: str,
    item: dict[str, Any],
    *,
    document: dict[str, Any] | None = None,
) -> RawEvidence:
    receipt_no = item["rcept_no"]
    report_name = item.get("report_nm", "")
    receipt_date = _format_receipt_date(item.get("rcept_dt"))
    document = document or {}
    document_text = document.get("document_text") or ""
    is_correction = _is_correction_disclosure(item)
    original_receipt_no = _original_receipt_no(item)
    return RawEvidence(
        source="DART",
        stock_code=stock_code,
        title=report_name,
        content=document_text or report_name,
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
            "disclosure_type": "correction" if is_correction else _infer_disclosure_type(report_name),
            "priority": "immediate" if is_correction else "batch",
            "priority_reason": "correction_disclosure" if is_correction else None,
            "is_correction": is_correction,
            "original_receipt_no": original_receipt_no,
            "correction_policy": "separate_event" if is_correction else None,
            "published_at": receipt_date,
            "source_name": "OpenDART",
            "filer_name": item.get("flr_nm"),
            "remark": item.get("rm"),
            "raw_payload": item,
            **document,
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


def _retryable_status_error(response: dict[str, Any]) -> DartApiError | None:
    status = response.get("status")
    if status is None:
        return None
    error = DartApiError.from_status(status, response.get("message", ""))
    if error.retryable:
        return error
    return None


def _is_correction_disclosure(item: dict[str, Any]) -> bool:
    report_name = str(item.get("report_nm") or "")
    remark = str(item.get("rm") or "")
    marker_text = f"{report_name} {remark}".lower()
    return any(
        marker in marker_text
        for marker in (
            "정정",
            "[correction]",
            "correction",
            "amendment",
        )
    )


def _original_receipt_no(item: dict[str, Any]) -> str | None:
    for key in ("org_rcept_no", "original_receipt_no", "original_rcept_no"):
        value = item.get(key)
        if value:
            return str(value).strip()
    return None


def _to_dart_error(exc: Exception) -> DartApiError:
    if isinstance(exc, DartApiError):
        return exc
    if isinstance(exc, HTTPError):
        retryable = exc.code >= 500 or exc.code == 429
        return DartApiError(
            f"DART HTTP error: {exc.code}",
            category="network",
            retryable=retryable,
        )
    if isinstance(exc, (TimeoutError, URLError)):
        return DartApiError(
            f"DART network error: {exc}",
            category="network",
            retryable=True,
        )
    return DartApiError(str(exc), category="unknown", retryable=False)


def _classify_dart_status(status: str) -> tuple[str, bool]:
    if status in {"010", "011", "012", "901"}:
        return "auth", False
    if status == "020":
        return "rate_limit", True
    if status in {"800", "900"}:
        return "service", True
    if status in {"014", "021", "100", "101"}:
        return "request", False
    if status == "013":
        return "no_data", False
    if status == "000":
        return "ok", False
    return "unknown", False


def _decode_document_bytes(value: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "cp949"):
        try:
            return value.decode(encoding)
        except UnicodeDecodeError:
            continue
    return value.decode("utf-8", errors="replace")


def _extract_xml_text(value: str) -> str:
    try:
        root = ElementTree.fromstring(value)
    except ElementTree.ParseError:
        return " ".join(value.split())
    return " ".join(text.strip() for text in root.itertext() if text and text.strip())


def _default_start_date() -> str:
    return (date.today() - timedelta(days=30)).strftime("%Y%m%d")


def _default_end_date() -> str:
    return date.today().strftime("%Y%m%d")
