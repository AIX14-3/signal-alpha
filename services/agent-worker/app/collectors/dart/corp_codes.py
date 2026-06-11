from __future__ import annotations

import asyncio
import io
import zipfile
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from xml.etree import ElementTree

from app.collectors.dart.disclosure import DartApiError


@dataclass(frozen=True)
class DartCorpCodeEntry:
    corp_code: str
    corp_name: str
    stock_code: str
    modify_date: str | None = None


class DartCorpCodeClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://opendart.fss.or.kr/api",
        timeout_seconds: int = 10,
    ) -> None:
        if not api_key:
            raise DartApiError("DART API key is required.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    async def fetch_corp_codes(self) -> list[DartCorpCodeEntry]:
        url = self.build_corp_code_url()
        body = await asyncio.to_thread(self._get_bytes, url)
        return parse_corp_code_zip(body)

    def build_corp_code_url(self) -> str:
        query = urlencode({"crtfc_key": self._api_key})
        return f"{self._base_url}/corpCode.xml?{query}"

    def _get_bytes(self, url: str) -> bytes:
        request = Request(url, headers={"Accept": "application/zip, application/octet-stream"})
        with urlopen(request, timeout=self._timeout_seconds) as response:
            return response.read()


def parse_corp_code_zip(body: bytes) -> list[DartCorpCodeEntry]:
    try:
        with zipfile.ZipFile(io.BytesIO(body)) as archive:
            xml_name = _find_xml_name(archive.namelist())
            xml_body = archive.read(xml_name)
    except zipfile.BadZipFile as exc:
        raise DartApiError("DART corpCode response is not a ZIP file.") from exc

    root = ElementTree.fromstring(xml_body)
    return [
        DartCorpCodeEntry(
            corp_code=_text(item, "corp_code"),
            corp_name=_text(item, "corp_name"),
            stock_code=_text(item, "stock_code"),
            modify_date=_text(item, "modify_date") or None,
        )
        for item in root.findall(".//list")
    ]


def _find_xml_name(names: list[str]) -> str:
    for name in names:
        if name.lower().endswith(".xml"):
            return name
    raise DartApiError("DART corpCode ZIP does not contain an XML file.")


def _text(item: Any, tag: str) -> str:
    value = item.findtext(tag)
    return value.strip() if value else ""
