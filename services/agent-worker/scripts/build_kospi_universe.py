"""Propose a broad KOSPI top-N market-cap universe for the hiring-ML study (FDR).

Read-only. Keeps **KOSPI common stocks only** (drops preferred shares, SPACs,
ETFs/REITs), ranks by market cap and takes the top N. Unlike ``select_universe_fdr.py``
(which keeps only R&D-intensive industries for the patent track), this is a *broad*
universe: hiring matching wants high recall across all sectors, and the data-driven
coverage threshold in the ML layer does the final selection. Always includes the
loaded anchors (005930/000660/035420).

Market cap = shares-outstanding × close. ``FinanceDataReader.StockListing`` gives
reliable **shares** (and tickers/names/market) but its real-time **Close/Marcap**
snapshot is unreliable in some environments, so close is taken from the (reliable)
``DataReader`` time series at ``--asof`` (one call per stock).

Writes both a CSV (review) and a JSON (seed input for ``seed_universe_stocks.py``).
No DB writes.

    uv run python scripts/build_kospi_universe.py --n 200 --asof 2023-12-28 \
        --out-csv universe_kospi200.csv --out-json universe_kospi200.json
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ANCHORS = ["005930", "000660", "035420"]

# English / abbreviated official names → a short_name that also appears in postings.
# Most KOSPI names are already the Korean legal name (what jasoseol carries), so we
# only override the handful that list under a romanized/abbreviated brand.
SHORT_NAME_OVERRIDES = {
    "035420": "네이버",      # NAVER
    "030200": "KT",
    "005490": "POSCO홀딩스",
    "003550": "LG",
    "034730": "SK",
}

# Names that mark a non-common-stock listing we never want in the universe.
_EXCLUDE_TOKENS = ("스팩", "리츠", "ETF", "ETN")


def _listing_rows() -> list[dict]:
    """Return [{ticker,name,market,shares}] from the KRX listing (reliable shares)."""
    import FinanceDataReader as fdr

    df = fdr.StockListing("KRX")
    cols = {c.lower(): c for c in df.columns}
    code = cols.get("code") or cols.get("symbol")
    name = cols.get("name")
    market = cols.get("market")
    shares = cols.get("stocks") or cols.get("shares")
    if not (code and name and shares):
        raise SystemExit(f"unexpected FDR columns: {list(df.columns)}")

    rows = []
    for _, r in df.iterrows():
        t = str(r[code]).zfill(6)
        if not t.isdigit():
            continue
        sh = r[shares]
        if sh != sh or not sh:  # NaN / 0
            continue
        rows.append({
            "ticker": t,
            "name": str(r[name]).strip(),
            "market": str(r[market]).strip() if market else "",
            "shares": float(sh),
        })
    return rows


def _close_at(ticker: str, asof: str) -> float | None:
    """Last close on/before ``asof`` from the reliable DataReader time series."""
    import FinanceDataReader as fdr

    start = f"{int(asof[:4]) - 1}{asof[4:]}"  # 1y lookback window
    try:
        df = fdr.DataReader(ticker, start, asof)
    except Exception:  # noqa: BLE001
        return None
    if df.empty or "Close" not in df.columns:
        return None
    close = df["Close"].iloc[-1]
    return float(close) if close == close and close else None


def _is_common_kospi(r: dict) -> bool:
    if "KOSPI" not in r["market"].upper():
        return False
    # Common stock codes end in '0'; preferred shares end in 5/7/9 etc.
    if not r["ticker"].endswith("0"):
        return False
    name = r["name"]
    if name.endswith("우") or name.endswith("우B") or "우선주" in name:
        return False
    if any(tok in name for tok in _EXCLUDE_TOKENS):
        return False
    return True


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Build broad KOSPI top-N universe (FDR)")
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--asof", default="2023-12-28", help="reference date for close (YYYY-MM-DD)")
    ap.add_argument("--pause", type=float, default=0.05, help="seconds between DataReader calls")
    ap.add_argument("--out-csv", default="universe_kospi200.csv")
    ap.add_argument("--out-json", default="universe_kospi200.json")
    args = ap.parse_args(argv)

    rows = _listing_rows()
    kospi = [r for r in rows if _is_common_kospi(r)]
    print(f"[fdr] {len(rows)} listed, {len(kospi)} KOSPI common stocks; "
          f"pricing as of {args.asof} (DataReader)...")

    priced = []
    for i, r in enumerate(kospi, 1):
        close = _close_at(r["ticker"], args.asof)
        if close is not None:
            r["marcap"] = r["shares"] * close
            priced.append(r)
        if i % 100 == 0:
            print(f"  priced {i}/{len(kospi)} ...")
        if args.pause:
            time.sleep(args.pause)
    kospi = priced
    kospi.sort(key=lambda r: r["marcap"], reverse=True)
    print(f"[price] {len(kospi)} stocks with a close on/before {args.asof}")

    by_ticker = {r["ticker"]: r for r in kospi}
    chosen: dict[str, dict] = {}
    for t in kospi[: args.n]:
        chosen[t["ticker"]] = t
    for a in ANCHORS:  # guarantee anchors even if outside top-N
        if a not in chosen and a in by_ticker:
            chosen[a] = by_ticker[a]
            print(f"[anchor] added {a} {by_ticker[a]['name']} (outside top-{args.n})")
        elif a not in by_ticker:
            print(f"[warn] anchor {a} not found in KOSPI common listing")

    universe = sorted(chosen.values(), key=lambda r: -r["marcap"])
    records = [
        {
            "ticker": r["ticker"],
            "name": r["name"],
            "short_name": SHORT_NAME_OVERRIDES.get(r["ticker"], r["name"]),
            "market": "KOSPI",
            "sector": None,  # FDR KOSPI listing has no sector; backfill later
            "marcap_won": int(r["marcap"]),
        }
        for r in universe
    ]

    Path(args.out_json).write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    with open(args.out_csv, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["ticker", "name", "short_name", "market", "sector", "marcap_won"])
        w.writeheader()
        for rec in records:
            w.writerow(rec)

    anchors_in = [a for a in ANCHORS if a in chosen]
    print(f"[universe] {len(records)} stocks  anchors={anchors_in}")
    for rec in records[:10]:
        print(f"  {rec['ticker']} {rec['name'][:16]:<16} {rec['marcap_won'] / 1e12:8.2f}조")
    print(f"  ... ({len(records)} total)")
    print(f"[out] {args.out_csv} / {args.out_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
