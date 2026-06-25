#!/usr/bin/env python
"""Collect daily close + VOLUME per ticker via pykrx (Stage 9 target-change test).

The local price CSV is close-only; the volatility/volume target test needs daily
VOLUME too. pykrx's OHLCV endpoint works without the KRX login that the investor
endpoints now require (verified), so it is the simplest volume source here.

Run with pykrx provided at runtime (not a repo dependency):
    uv run --with pykrx python scripts/collect_ohlcv_pykrx.py \
        --tickers 005930,000660,035420 --start 2016-01-01 --out daily_ohlcv.csv

Output CSV: ticker,date,close,volume  (one row per trading day). Local research
artifact — not committed, no prod writes.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, timedelta


def main() -> int:
    p = argparse.ArgumentParser(description="Collect daily close+volume via pykrx")
    p.add_argument("--tickers", default="005930,000660,035420")
    p.add_argument("--start", default="2016-01-01")
    p.add_argument("--end", default=None, help="default = yesterday")
    p.add_argument("--out", default="daily_ohlcv.csv")
    args = p.parse_args()

    from pykrx import stock

    start = args.start.replace("-", "")
    end = (args.end or (date.today() - timedelta(days=1)).isoformat()).replace("-", "")
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "date", "close", "volume"])
        for t in tickers:
            df = stock.get_market_ohlcv_by_date(start, end, t)
            n = 0
            for idx, row in df.iterrows():
                d = idx.date() if hasattr(idx, "date") else idx
                w.writerow([t, d.isoformat() if hasattr(d, "isoformat") else d,
                            row["종가"], row["거래량"]])
                n += 1
            print(f"{t}: {n} trading days [{start}..{end}]")
    print(f"[out] {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
