#!/usr/bin/env python
"""Build ``dart_krx250.csv`` (annual — and optionally quarterly — revenue) from OpenDART.

Reuses the SHIPPING DART collectors (read-only import; no DB): ``DartCorpCodeClient``
(ticker→corp_code), ``DartFinancialsClient`` (``fnlttSinglAcntAll``), and
``map_account`` (표준계정 → "revenue"). Output schema is exactly what
``app/ml/bakeoff_ab.load_annual_revenue`` reads:

    ticker,name,year,reprt,account,fs_div,amount

``account`` rows kept are the revenue line only; ``reprt`` is ``FY`` for annual
(reprt_code 11011). With ``--quarterly`` it also emits ``Q1/H1/Q3`` rows (for the
SUE-PEAD track's quarterly YoY); ``load_annual_revenue`` ignores non-``FY`` rows, so
one file serves both. ``amount`` is KRW (thstrm_amount, cumulative YTD as DART
reports it — compare same fiscal position across years for YoY).

    python scripts/build_dart_revenue_csv.py --universe krx_top250.json \
        --start-year 2015 --end-year 2024 --out dart_krx250.csv
    python scripts/build_dart_revenue_csv.py --universe krx_top250.json --quarterly ...
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# reprt_code → CSV reprt label (mirrors financials_api._REPRT_SUFFIX).
_ANNUAL = ("11011",)
_QUARTERLY = ("11013", "11012", "11014", "11011")  # Q1, H1, Q3, FY
_REPRT_LABEL = {"11013": "Q1", "11012": "H1", "11014": "Q3", "11011": "FY"}


def _load_universe(args) -> list[tuple[str, str]]:
    """Return [(ticker, name)] from universe JSON or --tickers (name filled later)."""
    if args.universe:
        data = json.loads(Path(args.universe).read_text(encoding="utf-8"))
        return [(str(r["ticker"]).strip(), str(r.get("name") or "").strip()) for r in data]
    return [(t.strip(), "") for t in args.tickers.split(",") if t.strip()]


async def _run(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build DART revenue CSV (OpenDART, no DB)")
    p.add_argument("--universe", default="", help="universe JSON (build_krx_universe.py)")
    p.add_argument("--tickers", default="005930,000660,035420", help="comma-sep if no --universe")
    p.add_argument("--start-year", type=int, default=2015)
    p.add_argument("--end-year", type=int, default=2024)
    p.add_argument("--quarterly", action="store_true", help="also emit Q1/H1/Q3 (for SUE-PEAD)")
    p.add_argument("--out", default="dart_krx250.csv")
    args = p.parse_args(argv)

    try:
        from dotenv import find_dotenv, load_dotenv
        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass
    api_key = os.environ.get("DART_API_KEY")
    if not api_key:
        raise SystemExit("DART_API_KEY unset — add it to the worktree .env")
    base_url = os.environ.get("DART_BASE_URL", "https://opendart.fss.or.kr/api")

    from app.collectors.dart.account_mapping import map_account
    from app.collectors.dart.corp_codes import DartCorpCodeClient
    from app.collectors.dart.financials_api import DartFinancialsClient, _to_krw

    universe = _load_universe(args)
    reprt_codes = _QUARTERLY if args.quarterly else _ANNUAL

    # 1) corp_code map (one big call), keyed by 6-digit stock_code.
    print("[corp] fetching corpCode.xml ...")
    entries = await DartCorpCodeClient(api_key=api_key, base_url=base_url).fetch_corp_codes()
    corp_by_ticker = {
        e.stock_code: (e.corp_code, e.corp_name)
        for e in entries if e.stock_code
    }
    print(f"[corp] {len(corp_by_ticker)} listed corp_codes")

    client = DartFinancialsClient(api_key=api_key, base_url=base_url, min_request_interval_sec=0.2)
    out_rows: list[tuple] = []
    missing = 0
    for ticker, uni_name in universe:
        mapped = corp_by_ticker.get(ticker)
        if not mapped:
            print(f"  ! {ticker}: no corp_code")
            missing += 1
            continue
        corp_code, corp_name = mapped
        name = uni_name or corp_name
        got = 0
        for year in range(args.start_year, args.end_year + 1):
            for reprt in reprt_codes:
                amount = await _fetch_revenue(client, corp_code, year, reprt, map_account, _to_krw)
                if amount is None:
                    continue
                out_rows.append((ticker, name, year, _REPRT_LABEL[reprt], "revenue",
                                 amount["fs_div"], amount["krw"]))
                got += 1
        print(f"  {ticker} {name}: {got} revenue rows")

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "name", "year", "reprt", "account", "fs_div", "amount"])
        w.writerows(out_rows)
    print(f"[done] {len(out_rows)} revenue rows, {missing} tickers missing corp_code -> {args.out}")
    return 0


async def _fetch_revenue(client, corp_code, year, reprt, map_account, to_krw) -> dict | None:
    """Fetch one (corp, year, reprt) and return {'krw','fs_div'} for the revenue line."""
    for fs_div in ("CFS", "OFS"):
        try:
            resp = await client.fetch_financials(
                corp_code=corp_code, bsns_year=year, reprt_code=reprt, fs_div=fs_div
            )
        except Exception as exc:  # noqa: BLE001 - skip a bad (corp,year); keep going
            print(f"    ~ {corp_code} {year} {reprt} {fs_div}: {type(exc).__name__}")
            continue
        status = resp.get("status")
        if status == "013":  # no data → try OFS, else next year
            continue
        if status != "000":
            continue
        best = None
        for item in resp.get("list", []):
            if map_account(item.get("account_id"), item.get("account_nm")) != "revenue":
                continue
            krw = to_krw(item.get("thstrm_amount"))
            if krw is None:
                continue
            # revenue can appear once; keep the largest (consolidated top line).
            if best is None or krw > best:
                best = krw
        if best is not None:
            return {"krw": best, "fs_div": fs_div}
    return None


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
