#!/usr/bin/env python
"""Backfill daily close+VOLUME to a CSV via FinanceDataReader (no API key, no DB).

Unlike ``backfill_prices_fdr.py`` (close only), this writes the 4-column
``ticker,date,close,volume`` schema that ``app/ml/prices_csv.load_prices_volume_csv``
requires — the magnitude/revenue real runs need volume. Reads the ticker list from
a universe JSON (build_krx_universe.py) or an explicit --tickers list, and appends a
benchmark index (KS11) so the same file can back the SUE-PEAD --benchmark-csv.

FDR historical ``DataReader`` (as-of) is reliable in this env even though the
realtime snapshot is not (memory fdr-stocklisting-snapshot-broken concerns the live
StockListing snapshot, not historical OHLCV).

    python scripts/build_prices_csv_fdr.py --universe krx_top250.json \
        --start 2016-01-01 --end 2023-12-31 --benchmark KS11 --out prices_krx250.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path


def _load_tickers(args) -> list[str]:
    if args.universe:
        data = json.loads(Path(args.universe).read_text(encoding="utf-8"))
        return [str(r["ticker"]).strip() for r in data if r.get("ticker")]
    return [t.strip() for t in args.tickers.split(",") if t.strip()]


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Backfill daily close+volume CSV (FDR)")
    p.add_argument("--universe", default="", help="universe JSON from build_krx_universe.py")
    p.add_argument("--tickers", default="005930,000660,035420", help="comma-sep (if no --universe)")
    p.add_argument("--benchmark", default="KS11", help="index ticker appended (KOSPI=KS11)")
    p.add_argument("--start", default="2016-01-01")
    p.add_argument("--end", default="2023-12-31")
    p.add_argument("--out", default="prices_krx250.csv")
    args = p.parse_args(argv)

    try:
        import FinanceDataReader as fdr
    except ImportError:
        raise SystemExit("FinanceDataReader not installed. Run: pip install finance-datareader")

    tickers = _load_tickers(args)
    if args.benchmark and args.benchmark not in tickers:
        tickers.append(args.benchmark)

    rows: list[tuple[str, str, float, float]] = []
    ok = failed = 0
    for ticker in tickers:
        try:
            df = fdr.DataReader(ticker, args.start, args.end)
        except Exception as exc:  # noqa: BLE001 - one bad ticker shouldn't sink the run
            print(f"  ! {ticker}: {type(exc).__name__}: {exc}")
            failed += 1
            continue
        if "Close" not in df.columns:
            print(f"  ! {ticker}: no Close column, skipped")
            failed += 1
            continue
        has_vol = "Volume" in df.columns
        n = 0
        for idx, r in df.iterrows():
            close = r.get("Close")
            if close is None or (isinstance(close, float) and math.isnan(close)):
                continue
            vol = r.get("Volume") if has_vol else 0.0
            if vol is None or (isinstance(vol, float) and math.isnan(vol)):
                vol = 0.0  # indices have no volume; keep the row for close/benchmark use
            day = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
            rows.append((ticker, day, float(close), float(vol)))
            n += 1
        ok += 1
        print(f"  {ticker}: {n} sessions")

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["ticker", "date", "close", "volume"])
        w.writerows(rows)
    print(f"[done] {len(rows)} rows, {ok} ok / {failed} failed -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
