#!/usr/bin/env python
"""Stage 3+4: collect DataLab search series for patent-extracted keywords (local).

Validation ("does this keyword have search data?") and collection are the SAME
Naver query, so they're merged. Each keyword is requested over the FULL range in
ONE weekly call -> consistent cross-year normalization (fixes the per-year max=100
artifact from year-chunked collection). Keywords with negligible search volume are
dropped (that's the validation). Local CSV only -- no prod writes.

    DATABASE not needed. Requires NAVER_CLIENT_ID / NAVER_CLIENT_SECRET (.env).
    python scripts/collect_datalab_for_keywords.py --kw-dir kw_out --out datalab_patent_keywords.csv
    python scripts/collect_datalab_for_keywords.py --limit 5   # smoke
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import glob
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def load_keywords(kw_dir: str) -> dict[str, list[str]]:
    by_ticker: dict[str, list[str]] = {}
    for f in glob.glob(str(Path(kw_dir) / "patent_keywords_*.json")):
        ticker = Path(f).stem.split("_")[-1]
        data = json.loads(Path(f).read_text(encoding="utf-8"))
        by_ticker[ticker] = sorted({k["keyword"] for k in data})
    return by_ticker


async def _run(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Collect DataLab series for patent keywords (local)")
    p.add_argument("--kw-dir", default="kw_out")
    p.add_argument("--start", default="2016-01-01")
    p.add_argument("--end", default=None, help="default = yesterday (Naver rejects future end dates)")
    p.add_argument("--time-unit", default="week", choices=["date", "week", "month"])
    p.add_argument("--out", default="datalab_patent_keywords.csv")
    p.add_argument("--limit", type=int, default=None, help="cap keywords per ticker (smoke test)")
    p.add_argument("--min-nonzero", type=int, default=4,
                   help="min non-zero points for a keyword to count as having search data")
    args = p.parse_args(argv)
    if not args.end:
        from datetime import date, timedelta
        args.end = (date.today() - timedelta(days=1)).isoformat()

    try:
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass
    cid = os.environ.get("NAVER_CLIENT_ID")
    csec = os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and csec):
        raise SystemExit("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET are required")

    from app.clients.naver_datalab_client import NaverDataLabClient, NaverDataLabError

    client = NaverDataLabClient(client_id=cid, client_secret=csec)
    by_ticker = load_keywords(args.kw_dir)
    if not by_ticker:
        raise SystemExit(f"no patent_keywords_*.json under {args.kw_dir}")

    # Resume: a sidecar records every keyword with a DEFINITIVE result (has-data OR
    # confirmed no-data). Quota/network failures are NOT recorded, so a re-run (e.g.
    # after the daily Naver quota resets) retries only the unfinished/failed ones.
    out_path = Path(args.out)
    done_path = out_path.with_suffix(out_path.suffix + ".done")
    done: set[str] = set()
    if done_path.exists():
        done = set(done_path.read_text(encoding="utf-8").splitlines())
    csv_exists = out_path.exists()

    csv_fh = open(out_path, "a", newline="", encoding="utf-8")
    writer = csv.writer(csv_fh)
    if not csv_exists:
        writer.writerow(["ticker", "keyword", "period", "ratio"])
    done_fh = open(done_path, "a", encoding="utf-8")

    total_valid = total_seen = failed = 0
    try:
        for ticker, kws in by_ticker.items():
            if args.limit:
                kws = kws[: args.limit]
            valid = dropped = skipped = 0
            for kw in kws:
                tag = f"{ticker}|{kw}"
                if tag in done:
                    skipped += 1
                    continue
                try:
                    recs = await client.search(
                        keyword_group=ticker, keywords=[kw],
                        start_date=args.start, end_date=args.end, time_unit=args.time_unit,
                    )
                except NaverDataLabError as exc:
                    print(f"  ! {ticker} {kw!r}: {exc}")  # quota/network -> retry next run
                    failed += 1
                    continue
                nonzero = [r for r in recs if r.ratio > 0]
                if len(nonzero) >= args.min_nonzero:
                    valid += 1
                    writer.writerows((ticker, kw, r.period, r.ratio) for r in recs)
                else:
                    dropped += 1
                done_fh.write(tag + "\n")
                done_fh.flush()
                done.add(tag)
            total_valid += valid
            total_seen += valid + dropped
            print(f"{ticker}: +{valid} with data, {dropped} no-data, {skipped} already-done (of {len(kws)})")
    finally:
        csv_fh.close()
        done_fh.close()

    print(f"\n[out] appended to {args.out}  (resume sidecar: {done_path.name})")
    print(f"this run: validated {total_valid}, {failed} failed (quota/network — re-run to retry)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
