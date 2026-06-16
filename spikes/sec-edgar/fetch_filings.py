"""SEC EDGAR 공시 목록 수집 타당성 스파이크.

목적: 해외(미국) 공시를 DART처럼 가져올 수 있는지 확인한다.
흐름: 티커 → CIK 매핑 → submissions API → 최근 공시 목록 파싱.

데이터가 정상 수신되면 이후 단계(테이블 생성 → ORM)로 진행한다.
이 스파이크는 DB·ORM 없이 순수 fetch/parse만 검증한다.

사용법:
    uv run python spikes/sec-edgar/fetch_filings.py            # 기본: AI 선도주 일부
    uv run python spikes/sec-edgar/fetch_filings.py NVDA AAPL  # 특정 티커
    uv run python spikes/sec-edgar/fetch_filings.py NVDA --forms 8-K,10-K,10-Q --limit 10

참고:
- SEC는 연락처가 담긴 User-Agent 헤더를 요구한다(없으면 차단). 환경변수 SEC_USER_AGENT로 덮어쓸 수 있다.
- 권장 호출률 ~10 req/s. 본 스파이크는 요청 간 짧은 간격을 둔다.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass

import httpx

# Windows 콘솔에서 한글 출력이 깨지지 않도록(레포 vector_store.py와 동일 패턴).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# SEC 공식 엔드포인트 (무료, API 키 불필요)
TICKER_MAP_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"

# 연락처를 포함해야 한다. 운영 시 팀 공용 주소로 교체.
DEFAULT_USER_AGENT = os.getenv(
    "SEC_USER_AGENT",
    "signal-alpha SEC EDGAR spike (contact: biop99999@gmail.com)",
)

# 기본 점검 대상 (AI 선도주 보드 중 미국 상장 일부)
DEFAULT_TICKERS = ["NVDA", "AMD", "MSFT"]

# 요청 간 최소 간격(초). SEC fair-access 준수.
REQUEST_INTERVAL_SEC = 0.2


@dataclass
class Filing:
    form: str
    filing_date: str
    accession_no: str
    primary_doc: str
    primary_desc: str

    def doc_url(self, cik_int: int) -> str:
        # accession 의 하이픈 제거 폴더 + primary document 파일명으로 원문 URL 구성.
        acc_nodash = self.accession_no.replace("-", "")
        return (
            f"https://www.sec.gov/Archives/edgar/data/{cik_int}/"
            f"{acc_nodash}/{self.primary_doc}"
        )


def _client(user_agent: str) -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": user_agent, "Accept-Encoding": "gzip, deflate"},
        timeout=15.0,
        follow_redirects=True,
    )


def load_ticker_to_cik(client: httpx.Client) -> dict[str, int]:
    """company_tickers.json → {TICKER(대문자): CIK(int)}."""
    resp = client.get(TICKER_MAP_URL)
    resp.raise_for_status()
    data = resp.json()
    # 형태: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    mapping: dict[str, int] = {}
    for row in data.values():
        mapping[str(row["ticker"]).upper()] = int(row["cik_str"])
    return mapping


def fetch_filings(
    client: httpx.Client,
    cik_int: int,
    *,
    forms: set[str] | None,
    limit: int,
) -> list[Filing]:
    """submissions API의 recent 공시를 파싱한다."""
    cik10 = f"{cik_int:010d}"
    resp = client.get(SUBMISSIONS_URL.format(cik10=cik10))
    resp.raise_for_status()
    payload = resp.json()
    recent = payload.get("filings", {}).get("recent", {})

    forms_col = recent.get("form", [])
    dates_col = recent.get("filingDate", [])
    acc_col = recent.get("accessionNumber", [])
    doc_col = recent.get("primaryDocument", [])
    desc_col = recent.get("primaryDocDescription", [])

    out: list[Filing] = []
    for i in range(len(forms_col)):
        form = forms_col[i]
        if forms and form not in forms:
            continue
        out.append(
            Filing(
                form=form,
                filing_date=dates_col[i] if i < len(dates_col) else "",
                accession_no=acc_col[i] if i < len(acc_col) else "",
                primary_doc=doc_col[i] if i < len(doc_col) else "",
                primary_desc=desc_col[i] if i < len(desc_col) else "",
            )
        )
        if len(out) >= limit:
            break
    return out


def run(tickers: list[str], forms: set[str] | None, limit: int, user_agent: str) -> int:
    print(f"User-Agent: {user_agent}")
    print(f"대상 티커: {', '.join(tickers)}  | 폼 필터: {sorted(forms) if forms else '전체'}  | limit: {limit}\n")

    with _client(user_agent) as client:
        try:
            tmap = load_ticker_to_cik(client)
        except httpx.HTTPError as exc:
            print(f"[FAIL] 티커→CIK 매핑 로드 실패: {exc}")
            return 1
        print(f"[OK] company_tickers.json 로드: {len(tmap):,}개 티커\n")

        total = 0
        for ticker in tickers:
            t = ticker.upper()
            cik = tmap.get(t)
            if cik is None:
                print(f"### {t}: CIK 미발견 (미국 상장 아님/티커 오타?)\n")
                continue
            time.sleep(REQUEST_INTERVAL_SEC)
            try:
                filings = fetch_filings(client, cik, forms=forms, limit=limit)
            except httpx.HTTPError as exc:
                print(f"### {t} (CIK {cik:010d}): submissions 조회 실패: {exc}\n")
                continue

            print(f"### {t}  CIK={cik:010d}  최근 공시 {len(filings)}건")
            print(f"{'FORM':<8} {'DATE':<12} {'ACCESSION':<22} DOC")
            for f in filings:
                print(f"{f.form:<8} {f.filing_date:<12} {f.accession_no:<22} {f.primary_doc}")
            if filings:
                print(f"예시 원문 URL: {filings[0].doc_url(cik)}")
            print()
            total += len(filings)

    print(f"[DONE] 총 {total}건 수신.")
    return 0 if total > 0 else 2


def main() -> int:
    parser = argparse.ArgumentParser(description="SEC EDGAR 공시 목록 수집 스파이크")
    parser.add_argument("tickers", nargs="*", default=[], help="티커 (없으면 기본 AI 선도주)")
    parser.add_argument("--forms", default="", help="쉼표구분 폼 필터 (예: 8-K,10-K,10-Q)")
    parser.add_argument("--limit", type=int, default=10, help="티커당 최대 공시 수")
    args = parser.parse_args()

    tickers = args.tickers or DEFAULT_TICKERS
    forms = {s.strip() for s in args.forms.split(",") if s.strip()} or None
    return run(tickers, forms, args.limit, DEFAULT_USER_AGENT)


if __name__ == "__main__":
    sys.exit(main())
