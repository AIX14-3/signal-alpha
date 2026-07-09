from __future__ import annotations

import asyncio
import time
import xml.etree.ElementTree as ET
from typing import Any
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_MAX_RETRIES = 3
_RETRY_BACKOFF_SECONDS = 1.0

# KIPRIS resultCode 22 = LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS (월 사용 한도 소진).
# 이 코드가 뜨면 이번 달은 같은 인증키로 더 못 부르므로 BigQuery 폴백 대상이 된다.
QUOTA_EXCEEDED_CODE = "22"


class KiprisApiError(RuntimeError):
    def __init__(self, message: str, *, result_code: str | None = None) -> None:
        super().__init__(message)
        self.result_code = result_code

    @property
    def is_quota_error(self) -> bool:
        """월 호출 한도 소진(resultCode 22) 여부 — BigQuery 폴백 트리거."""
        return self.result_code == QUOTA_EXCEEDED_CODE


class KiprisPatentRecord:
    __slots__ = (
        "application_no",
        "invention_title",
        "applicant_name",
        "application_date",
        "ipc_code",
        "open_date",
        "registration_number",
        "abstract",
        "raw",
    )

    def __init__(
        self,
        *,
        application_no: str,
        invention_title: str,
        applicant_name: str | None,
        application_date: str | None,
        ipc_code: str | None,
        open_date: str | None = None,
        registration_number: str | None = None,
        abstract: str | None = None,
        raw: dict[str, Any],
    ) -> None:
        self.application_no = application_no
        self.invention_title = invention_title
        self.applicant_name = applicant_name
        self.application_date = application_date
        self.ipc_code = ipc_code
        self.open_date = open_date
        self.registration_number = registration_number
        self.abstract = abstract
        self.raw = raw


class KiprisClient:
    BASE_URL = "https://plus.kipris.or.kr/openapi/rest/patUtiModInfoSearchSevice"

    def __init__(
        self,
        *,
        api_key: str,
        timeout_seconds: int = 15,
        page_size: int = 100,
    ) -> None:
        if not api_key:
            raise KiprisApiError("KIPRIS API key is required.")
        self._api_key = api_key
        self._timeout_seconds = timeout_seconds
        self._page_size = page_size

    async def search_by_applicant(
        self,
        *,
        applicant: str,
        start_date: str,
        end_date: str,
        page_no: int = 1,
    ) -> tuple[list[KiprisPatentRecord], int]:
        """Return (records, total_count) for one page of applicant search."""
        url = self._build_url(
            applicant=applicant,
            start_date=start_date,
            end_date=end_date,
            page_no=page_no,
        )
        xml_text = await asyncio.to_thread(self._get_xml, url)
        return self._parse_response(xml_text)

    def _build_url(
        self,
        *,
        applicant: str,
        start_date: str,
        end_date: str,
        page_no: int,
    ) -> str:
        params = urlencode(
            {
                "accessKey": self._api_key,
                "applicant": applicant,
                "startDate": start_date,
                "endDate": end_date,
                "startPage": str(page_no),
                "numOfRows": str(self._page_size),
                "sortSpec": "AD",
                "descSort": "true",
            }
        )
        return f"{self.BASE_URL}/applicantNameSearchInfo?{params}"

    def _get_xml(self, url: str) -> str:
        request = Request(url, headers={"Accept": "application/xml"})
        # Retry transient network/timeout failures; quota/business errors are
        # returned as a valid XML body and handled in _parse_response.
        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                with urlopen(request, timeout=self._timeout_seconds) as response:
                    return response.read().decode("utf-8")
            except (TimeoutError, URLError) as exc:
                last_error = exc
                if attempt < _MAX_RETRIES - 1:
                    time.sleep(_RETRY_BACKOFF_SECONDS * (attempt + 1))
        raise KiprisApiError(
            f"KIPRIS request failed after {_MAX_RETRIES} attempts: {last_error}"
        )

    def _parse_response(self, xml_text: str) -> tuple[list[KiprisPatentRecord], int]:
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            raise KiprisApiError(f"KIPRIS XML parse error: {exc}") from exc

        result_code = (
            _text(root, ".//resultCode")
            or _text(root, ".//ResultCode")
            or _text(root, ".//header/resultCode")
            or ""
        )
        if result_code not in ("00", "E0000", ""):
            result_msg = _text(root, ".//resultMsg") or _text(root, ".//ResultMsg") or ""
            raise KiprisApiError(
                f"KIPRIS API error {result_code}: {result_msg}", result_code=result_code
            )

        total_count = int(_text(root, ".//TotalSearchCount") or _text(root, ".//totalCount") or "0")
        records: list[KiprisPatentRecord] = []

        items = (
            root.findall(".//PatentUtilityInfo")
            or root.findall(".//item")
            or root.findall(".//patUtiModInfoSearchSeviceInfoBrief")
        )
        for item in items:
            raw: dict[str, Any] = {child.tag: child.text for child in item}
            application_no = (
                _text(item, "ApplicationNumber")
                or _text(item, "applicationNumber")
                or _text(item, "applno")
                or ""
            )
            if not application_no:
                continue
            invention_title = (
                _text(item, "InventionName")
                or _text(item, "inventionTitle")
                or _text(item, "inventionName")
                or _text(item, "title")
                or ""
            )
            records.append(
                KiprisPatentRecord(
                    application_no=application_no.strip(),
                    invention_title=invention_title.strip(),
                    applicant_name=_text(item, "Applicant") or _text(item, "applicantName"),
                    application_date=_text(item, "ApplicationDate") or _text(item, "applicationDate") or _text(item, "appDate"),
                    ipc_code=_text(item, "InternationalpatentclassificationNumber") or _text(item, "ipcCode") or _text(item, "ipc"),
                    open_date=_text(item, "OpeningDate") or _text(item, "openDate"),
                    registration_number=_text(item, "RegisterNumber") or _text(item, "registrationNumber"),
                    abstract=_text(item, "astrtCont") or _text(item, "abstract"),
                    raw=raw,
                )
            )

        return records, total_count


def _text(element: ET.Element, path: str) -> str | None:
    node = element.find(path)
    if node is None or node.text is None:
        return None
    return node.text.strip() or None


def _window_days(name: str, default: int) -> int:
    from os import getenv

    raw = getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _default_start_date() -> str:
    # applicantNameSearchInfo 의 startDate/endDate 는 **출원일(AD)** 창이다. 특허는
    # 출원 후 ~18개월 뒤 공개되므로, "지금 새로 공개돼 처음 보이는 특허"는 출원일이
    # 대략 12~24개월 전이다. 기존 기본값(어제 1일치)은 그 창을 완전히 벗어나 매일
    # 사실상 0건만 수집했다 → 출원일 창을 공개 지연을 덮도록 넓힌다(이미 적재분은
    # source_hash dedup 으로 skip). ⚠️ 창이 넓으면 대형 출원인은 KIPRIS 무료 월쿼터
    # (~1,000콜)를 넘길 수 있어 env 로 조절한다. 최근 공개분의 저비용 확보는
    # BigQuery(공개일 창, collect_patents_bigquery_daily.py)가 주로 담당한다.
    # 🔬 SPIKE(라이브 키 필요): KIPRIS 가 공개일(open date) 검색/정렬을 지원하면 출원일
    # 창 대신 그걸 써서 정확·저비용으로 최근 공개분만 받을 수 있다(docs 런북 참조).
    from datetime import date, timedelta

    return (date.today() - timedelta(days=_window_days("PATENT_KIPRIS_WINDOW_START_DAYS", 800))).strftime("%Y%m%d")


def _default_end_date() -> str:
    from datetime import date, timedelta

    return (date.today() - timedelta(days=_window_days("PATENT_KIPRIS_WINDOW_END_DAYS", 0))).strftime("%Y%m%d")
