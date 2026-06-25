"""Backfill daily closes to a local CSV via FinanceDataReader (no API key, no DB).

Pulls 2021-2023 daily prices for the test tickers plus a benchmark index and
writes a ``ticker,date,close`` CSV that the bake-off reads with ``--prices-csv``.
We deliberately do NOT write ``ohlcv_data`` (the price team's table) — this keeps
the DataLab-only experiment self-contained.

    pip install finance-datareader
    python scripts/backfill_prices_fdr.py \
        --tickers 005930,000660,035420 --benchmark KS11 \
        --start 2021-01-01 --end 2023-12-31 --out prices_2021_2023.csv

Then:
    DATABASE_URL=... python -m app.ml.research.bakeoff --source datalab-db \
        --prices-csv prices_2021_2023.csv --benchmark KS11
"""

from __future__ import annotations

import argparse
import csv


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Backfill daily closes to CSV (FinanceDataReader)")
    parser.add_argument("--tickers", default="005930,000660,035420", help="comma-separated")
    parser.add_argument("--benchmark", default="KS11", help="index ticker (KOSPI=KS11)")
    parser.add_argument("--start", default="2021-01-01")
    parser.add_argument("--end", default="2023-12-31")
    parser.add_argument("--out", default="prices_2021_2023.csv")
    args = parser.parse_args(argv)

    try:
        import FinanceDataReader as fdr
    except ImportError:
        raise SystemExit("FinanceDataReader is not installed. Run: pip install finance-datareader")

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    if args.benchmark and args.benchmark not in tickers:
        tickers.append(args.benchmark)

    rows: list[tuple[str, str, float]] = []
    for ticker in tickers:
        df = fdr.DataReader(ticker, args.start, args.end)
        if "Close" not in df.columns:
            print(f"  ! {ticker}: no Close column, skipped")
            continue
        for idx, close in df["Close"].items():
            if close is None:
                continue
            day = idx.date().isoformat() if hasattr(idx, "date") else str(idx)[:10]
            rows.append((ticker, day, float(close)))
        print(f"  {ticker}: {len(df)} sessions")

    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(["ticker", "date", "close"])
        writer.writerows(rows)
    print(f"[done] wrote {len(rows)} rows for {len(tickers)} tickers -> {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
