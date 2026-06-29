"""Stage 1a: precise-rematch hiring coverage → ML universe selection.

Read-only. Reuses ``app.ml.research.hiring_db`` precision contract
(``unique_norm_map`` / ``_norm_name``) so the selection matches the bake-off's
``--precise-rematch`` attribution exactly.

For every HIRING posting we re-attribute by EXACT normalized source_name to a
unique universe stock (ambiguous/non-universe dropped), then count postings per
(stock, year). A stock enters the ML universe iff it clears the threshold:
    >= MIN_POSTS total postings AND spread over >= MIN_YEARS distinct years.

Also cross-checks local price coverage (prices CSV) since the bake-off needs a
price series per ticker.

Run (from services/agent-worker):
    DATABASE_URL=... uv run --extra dev python scripts/hiring_coverage_report.py \
        --prices-csv "C:/Users/804/Documents/ML/Hiring/prices_kospi200.csv" \
        --min-posts 30 --min-years 3 \
        --out "C:/Users/804/Documents/ML/Hiring/hiring_coverage.csv"
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.research.hiring_db import _norm_name, unique_norm_map  # noqa: E402


def _load_db_url() -> str:
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    for p in [
        Path("C:/Users/804/Documents/GitHub/signal-alpha/.env"),
        Path.cwd() / ".env",
    ]:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("DATABASE_URL=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("DATABASE_URL not found")


def _price_tickers(prices_csv: str | None) -> set[str]:
    if not prices_csv or not Path(prices_csv).exists():
        return set()
    seen: set[str] = set()
    with open(prices_csv, encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            t = (row.get("ticker") or "").strip()
            if t:
                seen.add(t)
    return seen


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--prices-csv", default=None)
    ap.add_argument("--min-posts", type=int, default=30)
    ap.add_argument("--min-years", type=int, default=3)
    ap.add_argument("--out", default=None)
    args = ap.parse_args()

    import asyncpg

    url = _load_db_url()
    conn = await asyncpg.connect(url)
    try:
        srows = await conn.fetch(
            "SELECT id, ticker, name, short_name, sector FROM stocks"
        )
        meta = {
            int(r["id"]): (r["ticker"], r["name"], r["short_name"], r["sector"])
            for r in srows
        }
        unique_map = unique_norm_map(
            (r["id"], r["name"], r["short_name"]) for r in srows
        )

        hrows = await conn.fetch(
            """
            SELECT source_name,
                   EXTRACT(YEAR FROM (published_at AT TIME ZONE 'Asia/Seoul'))::int AS yr
            FROM raw_documents
            WHERE source_type = 'HIRING'
            """
        )
    finally:
        await conn.close()

    # per stock: year -> count
    by_stock: dict[int, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    matched = dropped = 0
    for r in hrows:
        sid = unique_map.get(_norm_name(r["source_name"] or ""))
        if sid is None:
            dropped += 1
            continue
        by_stock[sid][r["yr"]] += 1
        matched += 1

    price_set = _price_tickers(args.prices_csv)

    rows = []
    for sid, years in by_stock.items():
        ticker, name, short_name, sector = meta.get(sid, ("?", "?", "?", None))
        total = sum(years.values())
        n_years = len([y for y, c in years.items() if c > 0])
        has_price = ticker in price_set if price_set else None
        passes = total >= args.min_posts and n_years >= args.min_years
        rows.append({
            "ticker": ticker,
            "name": name,
            "sector": sector or "",
            "total_postings": total,
            "n_years": n_years,
            "year_min": min(years) if years else None,
            "year_max": max(years) if years else None,
            "has_price": has_price,
            "passes_threshold": passes,
        })

    rows.sort(key=lambda d: d["total_postings"], reverse=True)

    selected = [
        r for r in rows
        if r["passes_threshold"] and (r["has_price"] in (True, None))
    ]
    sel_no_price = [
        r for r in rows if r["passes_threshold"] and r["has_price"] is False
    ]

    print(f"=== precise-rematch coverage (min_posts>={args.min_posts}, "
          f"min_years>={args.min_years}) ===")
    print(f"postings matched={matched}  dropped(ambiguous/non-universe)={dropped}")
    print(f"stocks with >=1 matched posting: {len(rows)}")
    print(f"stocks passing threshold: "
          f"{len([r for r in rows if r['passes_threshold']])}")
    print(f"  of which WITH price series (ML universe): {len(selected)}")
    if sel_no_price:
        print(f"  WARNING dropped for NO price series: {len(sel_no_price)} -> "
              f"{[r['ticker'] for r in sel_no_price]}")

    print("\n=== ML universe (selected) ===")
    print(f"{'ticker':<8}{'total':>6}{'yrs':>5}  {'price':>5}  name")
    for r in selected:
        print(f"{r['ticker']:<8}{r['total_postings']:>6}{r['n_years']:>5}  "
              f"{str(r['has_price']):>5}  {r['name']}")
    tickers = [r["ticker"] for r in selected]
    print(f"\nselected N = {len(tickers)}")
    print("tickers =", ",".join(tickers))

    if args.out and rows:
        with open(args.out, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            w.writerows(rows)
        print(f"\nwrote full coverage -> {args.out}")
    elif args.out:
        print("\n(no matched postings — nothing written)")


if __name__ == "__main__":
    asyncio.run(main())
