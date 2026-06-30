"""Collect historical DART disclosure TITLES (report_nm) + dates (rcept_dt) per
ticker, for the PIT event-keyword direction test.

Public DART list API (read-only, local JSON) via OpenDartReader — does NOT touch
the team's app/**/dart/**. Each disclosure's rcept_dt is the point-in-time anchor
(DART disclosures are public on rcept_dt; no 18-month lag like patents), so a
keyword first seen at rcept_dt is honestly knowable from that date forward.

Output JSON: {ticker: [{"rcept_dt": "YYYYMMDD", "report_nm": "..."}, ...]} — the
shape DartTitleSource consumes. Loops year-by-year per ticker so long ranges never
overflow a single list response. Resumable: tickers already in the output are kept.

Usage:
    DART_API_KEY from C:/Users/804/Documents/GitHub/signal-alpha/.env
    uv run --with opendartreader --with python-dotenv python \
        scripts/collect_dart_disclosures.py --tickers-file kw_demand \
        --start-year 2016 --end-year 2023 --out dart_disclosures.json
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

ENV = "C:/Users/804/Documents/GitHub/signal-alpha/.env"


def _load_key() -> str:
    from dotenv import dotenv_values

    key = (dotenv_values(ENV).get("DART_API_KEY") or "").strip()
    if not key:
        raise SystemExit(f"DART_API_KEY empty in {ENV}")
    return key


def _tickers_from_kwdir(kwdir: str) -> list[str]:
    """Tickers inferred from patent_keywords_<ticker>.json filenames in a kw dir."""
    out = []
    for p in sorted(Path(kwdir).glob("patent_keywords_*.json")):
        out.append(p.stem.replace("patent_keywords_", ""))
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tickers", default="", help="comma-separated tickers")
    ap.add_argument("--tickers-file", default="", help="kw dir to infer tickers from")
    ap.add_argument("--start-year", type=int, default=2016)
    ap.add_argument("--end-year", type=int, default=2023)
    ap.add_argument("--out", default="dart_disclosures.json")
    args = ap.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    if args.tickers_file:
        tickers += _tickers_from_kwdir(args.tickers_file)
    tickers = list(dict.fromkeys(tickers))  # dedupe, keep order
    if not tickers:
        raise SystemExit("no tickers (use --tickers or --tickers-file)")

    import OpenDartReader

    dart = OpenDartReader(_load_key())

    out_path = Path(args.out)
    data: dict[str, list[dict]] = {}
    if out_path.exists():
        data = json.loads(out_path.read_text(encoding="utf-8"))

    for i, ticker in enumerate(tickers, 1):
        if ticker in data and data[ticker]:
            print(f"{ticker}: already-done ({len(data[ticker])})")
            continue
        rows: list[dict] = []
        for year in range(args.start_year, args.end_year + 1):
            try:
                df = dart.list(ticker, start=f"{year}-01-01", end=f"{year}-12-31", kind="")
            except Exception as exc:  # noqa: BLE001 - one bad year/ticker shouldn't kill the run
                print(f"  {ticker} {year}: ERROR {exc}")
                continue
            if df is None or len(df) == 0:
                continue
            for _, r in df.iterrows():
                rcept = str(r.get("rcept_dt", "")).replace("-", "").strip()
                name = str(r.get("report_nm", "")).strip()
                if len(rcept) >= 8 and name:
                    rows.append({"rcept_dt": rcept[:8], "report_nm": name})
            time.sleep(0.1)  # be polite to the DART endpoint
        data[ticker] = rows
        out_path.write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8"
        )
        print(f"{ticker}: +{len(rows)} disclosures [{i}/{len(tickers)}]")

    n_tot = sum(len(v) for v in data.values())
    print(f"\n[out] {out_path}  ({len(data)} tickers, {n_tot} disclosures)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
