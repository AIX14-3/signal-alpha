#!/usr/bin/env python
"""Build a KRX name-search universe JSON (top-N by market cap) via FinanceDataReader.

Output schema matches what the research scripts read (marcap_by_ticker in
scratch_conditional_reversal.py): a list of
``{"ticker","name","market","marcap_won"}`` objects, market-cap-descending.

⚠️ This environment's FDR realtime Close/Marcap snapshot is distorted (see memory
fdr-stocklisting-snapshot-broken), but the Marcap COLUMN still RANKS the large-caps
correctly (Samsung #1, SK hynix #2, …), which is all a top-N pilot universe needs.
Absolute marcap_won is FDR's value and only used for ranking / marcap terciles.

    python scripts/build_krx_universe.py --top 30 --out krx_top250.json
    python scripts/build_krx_universe.py --markets KOSPI,KOSDAQ --top 250
"""
from __future__ import annotations

import argparse
import json


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build top-N KRX universe JSON (FDR)")
    p.add_argument("--markets", default="KOSPI,KOSDAQ", help="comma-separated: KOSPI,KOSDAQ")
    p.add_argument("--top", type=int, default=30, help="keep top-N by market cap")
    p.add_argument("--include-preferred", action="store_true",
                   help="keep 우선주 (default: drop; commons end in 0, preferred in 5/7/9/K)")
    p.add_argument("--out", default="krx_top250.json")
    args = p.parse_args(argv)

    try:
        import FinanceDataReader as fdr
    except ImportError:
        raise SystemExit("FinanceDataReader not installed. Run: pip install finance-datareader")

    rows: list[dict] = []
    for market in [m.strip() for m in args.markets.split(",") if m.strip()]:
        df = fdr.StockListing(market)
        for _, r in df.iterrows():
            code = str(r.get("Code") or "").strip()
            name = str(r.get("Name") or "").strip()
            marcap = r.get("Marcap")
            # 6-digit KRX tickers only; drop ETFs/blanks and non-positive marcap.
            if len(code) != 6 or not code.isdigit() or not name:
                continue
            # 우선주(preferred) share the common's name/financials → drop for a clean
            # name-search universe (common tickers end in '0').
            if not args.include_preferred and code[-1] != "0":
                continue
            try:
                marcap = float(marcap)
            except (TypeError, ValueError):
                continue
            if marcap <= 0:
                continue
            rows.append({
                "ticker": code,
                "name": name,
                "market": market,
                "marcap_won": marcap,
            })

    # Rank market-cap-descending, keep top-N (dedup by ticker, keep richest).
    seen: dict[str, dict] = {}
    for r in sorted(rows, key=lambda x: x["marcap_won"], reverse=True):
        seen.setdefault(r["ticker"], r)
    universe = list(seen.values())[: args.top]

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(universe, fh, ensure_ascii=False, indent=2)
    print(f"[done] {len(universe)} tickers (top {args.top} of {len(seen)}) -> {args.out}")
    for r in universe[:5]:
        print(f"  {r['ticker']} {r['name']} ({r['market']}) marcap={r['marcap_won']:.3e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
