"""SEC EDGAR 공시 목록 수집기.

`spikes/sec-edgar/fetch_filings.py`의 검증된 흐름을 설정(core/config.py) 기반으로
승격한 운영 모듈. 엔드포인트·User-Agent·rate·폼 화이트리스트를 모두 변수/설정으로
처리한다(하드코딩 없음). DB·ORM 적재는 호출측(리포지토리)이 담당한다.

파싱 함수(parse_*)는 순수 함수라 네트워크 없이 단위 테스트할 수 있다.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from app.collectors.sec.cik_map import format_cik10, parse_ticker_map
from app.collectors.sec.targets import default_target_tickers
from app.core.config import Settings, get_settings

_SUBMISSIONS_PATH = "/submissions/CIK{cik10}.json"


@dataclass(frozen=True)
class SecFiling:
    cik: str
    ticker: str
    company_name: str | None
    form: str
    filing_date: str
    accession_no: str
    primary_document: str
    primary_doc_description: str
    document_url: str

    def as_row(self) -> dict[str, Any]:
        """SecFilingRepository.upsert_filings 입력 형태로 변환."""
        return asdict(self)


def build_document_url(cik: Any, accession_no: str, primary_document: str) -> str:
    """accession + primary document로 원문 URL을 동적 조립(하드코딩 경로 없음)."""
    cik_int = int(str(cik).strip())
    acc_nodash = accession_no.replace("-", "")
    return (
        f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
        f"{acc_nodash}/{primary_document}"
    )


def parse_recent_filings(
    payload: Any,
    *,
    ticker: str,
    cik_int: int,
    forms: Iterable[str] | None,
    limit: int,
) -> list[SecFiling]:
    """submissions payload의 filings.recent를 SecFiling 목록으로 파싱(순수)."""
    form_filter = {f for f in forms} if forms else None
    company_name = payload.get("name")
    recent = payload.get("filings", {}).get("recent", {})

    forms_col = recent.get("form", [])
    dates_col = recent.get("filingDate", [])
    acc_col = recent.get("accessionNumber", [])
    doc_col = recent.get("primaryDocument", [])
    desc_col = recent.get("primaryDocDescription", [])

    cik10 = format_cik10(cik_int)
    out: list[SecFiling] = []
    for i in range(len(forms_col)):
        form = forms_col[i]
        if form_filter and form not in form_filter:
            continue
        accession_no = acc_col[i] if i < len(acc_col) else ""
        primary_document = doc_col[i] if i < len(doc_col) else ""
        out.append(
            SecFiling(
                cik=cik10,
                ticker=ticker.upper(),
                company_name=company_name,
                form=form,
                filing_date=dates_col[i] if i < len(dates_col) else "",
                accession_no=accession_no,
                primary_document=primary_document,
                primary_doc_description=desc_col[i] if i < len(desc_col) else "",
                document_url=build_document_url(cik_int, accession_no, primary_document),
            )
        )
        if len(out) >= limit:
            break
    return out


class SecEdgarClient:
    """SEC EDGAR HTTP 클라이언트. 설정 기반, fair-access rate 준수."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._owns_client = client is None
        self._client = client or httpx.Client(
            headers={
                "User-Agent": self._settings.sec_user_agent,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=self._settings.sec_timeout_seconds,
            follow_redirects=True,
        )
        self._last_request_ts = 0.0

    def _throttle(self) -> None:
        interval = self._settings.sec_min_request_interval_sec
        elapsed = time.monotonic() - self._last_request_ts
        if elapsed < interval:
            time.sleep(interval - elapsed)
        self._last_request_ts = time.monotonic()

    def _get_json(self, url: str) -> Any:
        retries = max(0, self._settings.sec_max_retries)
        last_exc: Exception | None = None
        for attempt in range(retries + 1):
            self._throttle()
            try:
                resp = self._client.get(url)
                resp.raise_for_status()
                return resp.json()
            except httpx.HTTPError as exc:  # noqa: PERF203 - 재시도 루프
                last_exc = exc
                if attempt < retries:
                    time.sleep(self._settings.sec_min_request_interval_sec * (attempt + 1))
        raise last_exc  # type: ignore[misc]

    def load_ticker_to_cik(self) -> dict[str, int]:
        return parse_ticker_map(self._get_json(self._settings.sec_ticker_map_url))

    def fetch_filings_by_cik(
        self,
        cik: Any,
        ticker: str,
        *,
        forms: Iterable[str] | None = None,
        limit: int = 50,
    ) -> list[SecFiling]:
        cik_int = int(str(cik).strip())
        url = self._settings.sec_base_url + _SUBMISSIONS_PATH.format(cik10=format_cik10(cik_int))
        payload = self._get_json(url)
        if forms is None:
            forms = self._settings.sec_form_whitelist or None
        return parse_recent_filings(
            payload, ticker=ticker, cik_int=cik_int, forms=forms, limit=limit
        )

    def fetch_filings(
        self,
        ticker: str,
        *,
        forms: Iterable[str] | None = None,
        limit: int = 50,
        cik_map: dict[str, int] | None = None,
    ) -> list[SecFiling]:
        """티커로 공시 목록 조회. cik_map 미제공 시 매핑을 1회 로드한다."""
        mapping = cik_map if cik_map is not None else self.load_ticker_to_cik()
        cik = mapping.get(ticker.upper())
        if cik is None:
            return []  # 비상장/미국 외 → 빈 결과 (호출측에서 우회 수집 결정)
        return self.fetch_filings_by_cik(cik, ticker, forms=forms, limit=limit)

    def resolve_target_tickers(self) -> list[str]:
        """수집 대상 티커. 설정(SEC_TARGET_TICKERS)이 있으면 그것을, 없으면 기본 유니버스."""
        return self._settings.sec_target_tickers or default_target_tickers()

    def fetch_target_filings(
        self,
        *,
        tickers: list[str] | None = None,
        forms: Iterable[str] | None = None,
        limit: int = 50,
    ) -> dict[str, list[SecFiling]]:
        """대상 유니버스 전체의 공시를 수집한다. 티커→CIK 매핑은 1회만 로드한다.

        비상장/미국 외 등 CIK 미발견 티커는 빈 리스트로 반환된다(호출측에서 우회 결정).
        """
        target_tickers = tickers if tickers is not None else self.resolve_target_tickers()
        cik_map = self.load_ticker_to_cik()
        return {
            ticker: self.fetch_filings(ticker, forms=forms, limit=limit, cik_map=cik_map)
            for ticker in target_tickers
        }

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> SecEdgarClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
