from __future__ import annotations

import asyncio
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen


class KiprisApiError(RuntimeError):
    pass


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
        with urlopen(request, timeout=self._timeout_seconds) as response:
            return response.read().decode("utf-8")

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
            raise KiprisApiError(f"KIPRIS API error {result_code}: {result_msg}")

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


def _default_start_date() -> str:
    from datetime import date, timedelta

    return (date.today() - timedelta(days=1)).strftime("%Y%m%d")


def _default_end_date() -> str:
    from datetime import date, timedelta

    return (date.today() - timedelta(days=1)).strftime("%Y%m%d")
