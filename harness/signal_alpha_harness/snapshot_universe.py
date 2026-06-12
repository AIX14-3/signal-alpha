"""KOSPI200 구성종목 스냅샷 생성 (1회 실행 후 결과 CSV를 커밋).

Usage (from harness/):

    uv run python -m signal_alpha_harness.snapshot_universe

KRX는 과거 시점의 지수 구성내역을 제공하지 않으므로 이 스냅샷은 실행일 기준
생존 종목이다 — 생존 편향 한계는 universe.py docstring과 신뢰도 보고서에 명시.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date
from pathlib import Path

from signal_alpha_harness.universe import DATA_DIR

KOSPI200_INDEX_CODE = "1028"


def fetch_kospi200_pykrx(pause_sec: float = 0.3) -> list[tuple[str, str]]:
    from pykrx import stock as krx

    codes = list(krx.get_index_portfolio_deposit_file(KOSPI200_INDEX_CODE))
    if not codes:
        raise RuntimeError("KRX 지수 PDF 응답이 비어 있음")
    rows: list[tuple[str, str]] = []
    for code in codes:
        name = krx.get_market_ticker_name(code)
        rows.append((code, str(name)))
        time.sleep(pause_sec)
    return rows


def fetch_kospi200_naver(pause_sec: float = 0.3, max_pages: int = 30) -> list[tuple[str, str]]:
    """네이버 금융 KOSPI200 편입종목 페이지 (10종목/페이지) — KRX 지수 PDF가
    익명 접근을 막은 경우의 폴백. 실제 지수 편입 기준이라 시총 상위 200 근사보다 정확."""
    import re

    import requests

    pattern = re.compile(r'href="/item/main\.naver\?code=(\d{6})"[^>]*>([^<]+)</a>')
    seen: dict[str, str] = {}
    for page in range(1, max_pages + 1):
        response = requests.get(
            f"https://finance.naver.com/sise/entryJongmok.naver?type=KPI200&page={page}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        response.raise_for_status()
        response.encoding = "euc-kr"
        matches = pattern.findall(response.text)
        if not matches:
            break
        before = len(seen)
        for code, name in matches:
            seen.setdefault(code, name.strip())
        if len(seen) == before:  # 마지막 페이지 반복 방어
            break
        time.sleep(pause_sec)
    if len(seen) < 150:
        raise RuntimeError(f"KOSPI200 목록이 불완전합니다 ({len(seen)}종목) — 페이지 구조 변경 의심")
    return sorted(seen.items())


def fetch_kospi200() -> list[tuple[str, str]]:
    try:
        return fetch_kospi200_pykrx()
    except Exception as error:
        print(f"pykrx 지수 PDF 실패({error}) — 네이버 금융 편입종목으로 폴백")
        return fetch_kospi200_naver()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="KOSPI200 유니버스 스냅샷 생성")
    parser.add_argument("--out-dir", type=Path, default=DATA_DIR)
    args = parser.parse_args(argv)

    as_of = date.today().strftime("%Y%m%d")
    rows = fetch_kospi200()
    out = args.out_dir / f"universe_kospi200_{as_of}.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["ticker", "name", "as_of"])
        for ticker, name in sorted(rows):
            writer.writerow([ticker, name, as_of])
    print(f"saved {len(rows)} tickers -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
