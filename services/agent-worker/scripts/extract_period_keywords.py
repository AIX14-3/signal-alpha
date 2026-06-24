#!/usr/bin/env python
"""Driver: patent-title JSON -> period-trending keywords per ticker (local artifact).

Reads the ``backfill_patents_bigquery.py --out`` JSON, runs the frequency-surge
extractor per ticker, and writes ``patent_keywords_<ticker>.json`` plus a short
console sanity summary (the last two periods' top terms).

    python scripts/extract_period_keywords.py --in patents_3stock.json --out-dir kw_out
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.keywords.extract import extract_period_keywords  # noqa: E402
from app.ml.keywords.sources import PatentTitleSource  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Extract period-trending keywords from patent titles")
    p.add_argument("--in", dest="inp", required=True, help="patents JSON (backfill_patents_bigquery --out)")
    p.add_argument("--out-dir", default="kw_out")
    p.add_argument("--months", type=int, default=6, choices=[3, 6, 12], help="period bucket size")
    p.add_argument("--top-k", type=int, default=15)
    p.add_argument("--min-count", type=int, default=3)
    args = p.parse_args(argv)

    src = PatentTitleSource(args.inp)
    outdir = Path(args.out_dir)
    outdir.mkdir(exist_ok=True)

    for ticker in src.tickers():
        kws = extract_period_keywords(
            src.records(ticker), months=args.months, top_k=args.top_k, min_count=args.min_count
        )
        out_path = outdir / f"patent_keywords_{ticker}.json"
        out_path.write_text(
            json.dumps([asdict(k) for k in kws], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        periods = sorted({k.period for k in kws})
        print(f"{ticker}: {len(kws)} keywords / {len(periods)} periods -> {out_path.name}")
        for per in periods[-3:]:
            top = [k.keyword for k in kws if k.period == per][:8]
            print(f"   {per}: {', '.join(top)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
