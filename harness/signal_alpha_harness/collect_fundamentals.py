"""DART 정형 재무 수집 (point-in-time) — 밸류·퀄리티 팩터의 원천.

Usage (from harness/):

    uv run python -m signal_alpha_harness.collect_fundamentals --from-year 2015
    uv run python -m signal_alpha_harness.collect_fundamentals --tickers 005930,000660  # 샘플 검증

핵심 규율: 모든 행에 **available_date(공시 접수일)** 를 붙인다. 팩터 계산은
``trade_date >= available_date`` 인 최신 행만 사용해야 미래정보 누수가 없다.
접수일은 ``rcept_no`` 앞 8자리(YYYYMMDD)에서 얻는다.

DART 정형 재무 API(fnlttSinglAcntAll)는 XBRL 의무화 이후 **2015사업연도부터**
제공된다 — 그 이전은 수집 불가(설계 문서의 10년 채택 근거 중 하나).

수집량: 200종목 × ~12년 × 4보고서 ≈ 9,600 호출 (일 쿼터 20,000건 내, 1~2시간).
종목별 샤드(data/fund_shards/{ticker}.parquet) + 재개 구조.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
import zipfile
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen
from xml.etree import ElementTree

import pandas as pd

from signal_alpha_harness.universe import DATA_DIR, load_universe

DART_BASE = "https://opendart.fss.or.kr/api"
DEFAULT_OUT = DATA_DIR / "fundamentals_kospi200.parquet"
DEFAULT_SHARD_DIR = DATA_DIR / "fund_shards"
CORP_CODE_CACHE = DATA_DIR / "dart_corp_codes.csv"

# reprt_code → (period_type, 회계기간 말일 month-day)
REPORTS = {
    "11013": ("Q1", "03-31"),
    "11012": ("H1", "06-30"),
    "11014": ("Q3", "09-30"),
    "11011": ("FY", "12-31"),
}

# account_nm 표기 변형 → 표준 컬럼
ACCOUNT_MAP = {
    "매출액": "revenue",
    "수익(매출액)": "revenue",
    "영업이익": "operating_income",
    "영업이익(손실)": "operating_income",
    "당기순이익": "net_income",
    "당기순이익(손실)": "net_income",
    "자본총계": "total_equity",
    "부채총계": "total_liabilities",
}

FUND_COLUMNS = [
    "ticker",
    "corp_code",
    "bsns_year",
    "period_type",
    "fiscal_date",
    "available_date",
    "fs_div",
    "revenue",
    "operating_income",
    "net_income",
    "total_equity",
    "total_liabilities",
]


def _api_key() -> str:
    import os

    key = os.environ.get("DART_API_KEY", "")
    if not key:
        # 루트 .env 폴백 (harness는 서비스 코드에 의존하지 않는다)
        env_path = Path(__file__).resolve().parents[2] / ".env"
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("DART_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"')
                    break
    if not key:
        raise SystemExit("DART_API_KEY가 필요합니다 (env 또는 루트 .env).")
    return key


def _get_json(endpoint: str, params: dict, retries: int = 2) -> dict:
    import json

    url = f"{DART_BASE}/{endpoint}?{urlencode(params)}"
    for attempt in range(retries + 1):
        try:
            with urlopen(url, timeout=15) as response:
                return json.loads(response.read().decode("utf-8"))
        except Exception:
            if attempt == retries:
                raise
            time.sleep(2.0 * (attempt + 1))
    raise AssertionError("unreachable")


def load_corp_codes(api_key: str, cache: Path = CORP_CODE_CACHE) -> dict[str, str]:
    """stock_code(6자리) → corp_code 매핑. 1회 다운로드 후 CSV 캐시."""
    if cache.exists():
        with cache.open(encoding="utf-8", newline="") as f:
            return {row["stock_code"]: row["corp_code"] for row in csv.DictReader(f)}

    url = f"{DART_BASE}/corpCode.xml?{urlencode({'crtfc_key': api_key})}"
    with urlopen(url, timeout=60) as response:
        payload = response.read()
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        xml_bytes = archive.read(archive.namelist()[0])
    mapping: dict[str, str] = {}
    for element in ElementTree.fromstring(xml_bytes).iter("list"):
        stock_code = (element.findtext("stock_code") or "").strip()
        corp_code = (element.findtext("corp_code") or "").strip()
        if len(stock_code) == 6:  # 상장사만
            mapping[stock_code] = corp_code

    cache.parent.mkdir(parents=True, exist_ok=True)
    with cache.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["stock_code", "corp_code"])
        for stock_code, corp_code in sorted(mapping.items()):
            writer.writerow([stock_code, corp_code])
    return mapping


def _parse_amount(raw: str | None) -> float | None:
    if not raw:
        return None
    cleaned = str(raw).replace(",", "").strip()
    if cleaned in ("", "-"):
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def fetch_period(
    api_key: str, corp_code: str, bsns_year: int, reprt_code: str, pause_sec: float = 0.15
) -> dict | None:
    """한 (연도, 보고서)의 표준 계정 추출. 연결(CFS) 우선, 없으면 별도(OFS)."""
    period_type, month_day = REPORTS[reprt_code]
    for fs_div in ("CFS", "OFS"):
        body = _get_json(
            "fnlttSinglAcntAll.json",
            {
                "crtfc_key": api_key,
                "corp_code": corp_code,
                "bsns_year": str(bsns_year),
                "reprt_code": reprt_code,
                "fs_div": fs_div,
            },
        )
        time.sleep(pause_sec)
        if body.get("status") == "013":  # 해당 데이터 없음
            continue
        if body.get("status") != "000":
            raise RuntimeError(f"DART 오류 status={body.get('status')} msg={body.get('message')}")

        row: dict = {
            "bsns_year": bsns_year,
            "period_type": period_type,
            "fiscal_date": f"{bsns_year}-{month_day}",
            "fs_div": fs_div,
            "available_date": None,
        }
        for item in body.get("list", []):
            column = ACCOUNT_MAP.get((item.get("account_nm") or "").strip())
            if column and column not in row:
                row[column] = _parse_amount(item.get("thstrm_amount"))
            rcept_no = item.get("rcept_no") or ""
            if row["available_date"] is None and len(rcept_no) >= 8:
                d = rcept_no[:8]
                row["available_date"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}"
        return row
    return None


def collect_ticker(
    api_key: str, ticker: str, corp_code: str, from_year: int, to_year: int
) -> pd.DataFrame:
    rows: list[dict] = []
    for year in range(from_year, to_year + 1):
        for reprt_code in REPORTS:
            row = fetch_period(api_key, corp_code, year, reprt_code)
            if row is not None:
                row["ticker"] = ticker
                row["corp_code"] = corp_code
                rows.append(row)
    frame = pd.DataFrame(rows)
    for column in FUND_COLUMNS:
        if column not in frame.columns:
            frame[column] = pd.NA
    return frame[FUND_COLUMNS]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="DART point-in-time 재무 수집")
    parser.add_argument("--from-year", type=int, default=2015)
    parser.add_argument("--to-year", type=int, default=date.today().year)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--shard-dir", type=Path, default=DEFAULT_SHARD_DIR)
    parser.add_argument("--tickers", type=str, default="", help="쉼표 구분 — 샘플 검증용 부분 수집")
    args = parser.parse_args(argv)

    api_key = _api_key()
    corp_codes = load_corp_codes(api_key)
    universe = load_universe()
    if args.tickers:
        wanted = {t.strip() for t in args.tickers.split(",")}
        universe = [s for s in universe if s.ticker in wanted]

    args.shard_dir.mkdir(parents=True, exist_ok=True)
    shards: list[Path] = []
    for position, stock in enumerate(universe, start=1):
        shard = args.shard_dir / f"{stock.ticker}.parquet"
        if shard.exists():
            shards.append(shard)
            continue
        corp_code = corp_codes.get(stock.ticker)
        if not corp_code:
            print(f"[{position:3d}/{len(universe)}] {stock.name} ({stock.ticker}): corp_code 없음 — 스킵")
            continue
        frame = collect_ticker(api_key, stock.ticker, corp_code, args.from_year, args.to_year)
        frame.to_parquet(shard, index=False)
        shards.append(shard)
        print(f"[{position:3d}/{len(universe)}] {stock.name} ({stock.ticker}): {len(frame)} periods")

    merged = pd.concat([pd.read_parquet(s) for s in shards], ignore_index=True)
    merged = merged.sort_values(["ticker", "fiscal_date"]).reset_index(drop=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    merged.to_parquet(args.out, index=False)
    print(f"saved {len(merged)} period-rows x {merged['ticker'].nunique()} tickers -> {args.out}")
    missing_available = merged["available_date"].isna().mean()
    if missing_available > 0:
        print(f"warning: available_date 결측 {missing_available:.1%} — point-in-time join에서 제외됨")
    return 0


if __name__ == "__main__":
    sys.exit(main())
