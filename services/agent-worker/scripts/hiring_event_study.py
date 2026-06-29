"""Event study: abnormal return (CAR) around HIRING posting dates.

Answers the user's micro question directly: **when a job posting appears, does the
stock move abnormally around that day — and does the move come BEFORE (lead),
ON (coincident), or AFTER (lag = tradeable) the posting?**

- AR_t = stock_return_t - benchmark_return_t (market model, beta=1).
- Event = a (stock, observed_date) with >=1 HIRING posting that KST publish day.
- Stock attribution = precise normalized source_name match to the ML universe
  (reuses hiring_db.unique_norm_map; mis-attributed names dropped).
- CAR path over [-W, +W] trading days; event day 0 = first trading day on/after
  observed_date. Look-ahead safe: observed_date is when the posting was public.

Headline reads:
  CAR[-W..-1] = pre-event drift (LEAD if !=0)
  AR(0)       = jump ON the posting day (COINCIDENT)
  CAR[+1..+W] = post-event drift (LAG / predictive if !=0, |t|>~2)

Buckets: burst size (#postings that day) and duty tech-share (tech-heavy vs not).

    DATABASE_URL=... uv run --extra dev python scripts/hiring_event_study.py \
        --prices-csv prices_kospi200.csv --benchmark KS11 --window 10 \
        --tickers <comma> --start 2016-01-01 --end 2023-12-31
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import statistics
import sys
from collections import defaultdict
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.research.hiring_db import _norm_name, unique_norm_map  # noqa: E402
from app.ml.research.hiring_dataset import duty_tally  # noqa: E402


def _db() -> str:
    if os.getenv("DATABASE_URL"):
        return os.environ["DATABASE_URL"]
    for p in [Path("C:/Users/804/Documents/GitHub/signal-alpha/.env"), Path.cwd() / ".env"]:
        if p.exists():
            for line in p.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("DATABASE_URL=") and not line.startswith("#"):
                    return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise SystemExit("no DATABASE_URL")


def _load_prices(path: str) -> dict[str, list[tuple[date, float]]]:
    out: dict[str, list[tuple[date, float]]] = defaultdict(list)
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                d = date.fromisoformat(str(row["date"])[:10])
                c = float(row["close"])
            except (ValueError, KeyError, TypeError):
                continue
            out[(row.get("ticker") or "").strip()].append((d, c))
    return {t: sorted(set(ps)) for t, ps in out.items()}


def _returns(series: list[tuple[date, float]]) -> tuple[list[date], dict[date, float]]:
    ret: dict[date, float] = {}
    for i in range(1, len(series)):
        prev, cur = series[i - 1][1], series[i][1]
        if prev:
            ret[series[i][0]] = cur / prev - 1.0
    return [d for d, _ in series], ret


def _car_for_event(event_day, axis, ar, window):
    pos = next((i for i, d in enumerate(axis) if d >= event_day), None)
    if pos is None or pos - window < 0 or pos + window >= len(axis):
        return None
    return [ar.get(axis[pos + k], 0.0) for k in range(-window, window + 1)]


async def _fetch_events(url, tickers):
    """Per ticker: list of (observed_date, n_postings, tech_share) from precise rematch."""
    import asyncpg
    conn = await asyncpg.connect(url)
    try:
        srows = await conn.fetch(
            "SELECT id, ticker, name, short_name FROM stocks WHERE ticker = ANY($1::text[])",
            tickers,
        )
        ticker_by_id = {int(r["id"]): r["ticker"] for r in srows}
        umap = unique_norm_map((r["id"], r["name"], r["short_name"]) for r in srows)
        rows = await conn.fetch(
            """
            SELECT r.source_name,
                   (r.published_at AT TIME ZONE 'Asia/Seoul')::date AS d,
                   d2.extra_payload->'duty_groups' AS dg
            FROM raw_documents r
            LEFT JOIN hiring_raw_details d2 ON d2.raw_document_id = r.id
            WHERE r.source_type='HIRING'
            """
        )
    finally:
        await conn.close()

    import json
    by_day: dict[tuple[str, date], list[tuple[int, int]]] = defaultdict(list)
    for r in rows:
        sid = umap.get(_norm_name(r["source_name"] or ""))
        if sid is None:
            continue
        t = ticker_by_id.get(sid)
        if not t:
            continue
        dg = r["dg"]
        if isinstance(dg, str):
            try:
                dg = json.loads(dg)
            except ValueError:
                dg = []
        tech, tot = duty_tally(dg if isinstance(dg, list) else [])
        by_day[(t, r["d"])].append((tech, tot))
    out: dict[str, list[tuple[date, int, float]]] = defaultdict(list)
    for (t, d), tallies in by_day.items():
        n = len(tallies)
        tech = sum(a for a, _ in tallies)
        tot = sum(b for _, b in tallies)
        share = tech / tot if tot > 0 else float("nan")
        out[t].append((d, n, share))
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="HIRING posting-date event study (CAR)")
    ap.add_argument("--prices-csv", required=True)
    ap.add_argument("--benchmark", default="KS11")
    ap.add_argument("--tickers", required=True)
    ap.add_argument("--window", type=int, default=10)
    ap.add_argument("--start", default="2016-01-01")
    ap.add_argument("--end", default="2023-12-31")
    args = ap.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    lo, hi = date.fromisoformat(args.start), date.fromisoformat(args.end)
    prices = _load_prices(args.prices_csv)
    if args.benchmark not in prices:
        raise SystemExit(f"benchmark {args.benchmark} not in prices")
    _, bench_ret = _returns(prices[args.benchmark])

    ar_by_ticker = {}
    for t in tickers:
        if t not in prices:
            continue
        axis, ret = _returns(prices[t])
        ar_by_ticker[t] = (axis, {d: r - bench_ret.get(d, 0.0) for d, r in ret.items()})

    events = asyncio.run(_fetch_events(_db(), tickers))
    W = args.window
    shares = [s for evs in events.values() for (_, _, s) in evs if s == s]
    share_med = statistics.median(shares) if shares else None

    paths: list[list[float]] = []
    burst = {"single": [], "burst(>=5)": []}
    techb = {"tech_hi": [], "tech_lo": []}
    for t, evs in events.items():
        if t not in ar_by_ticker:
            continue
        axis, abn = ar_by_ticker[t]
        for d, n, share in evs:
            if not (lo <= d <= hi):
                continue
            p = _car_for_event(d, axis, abn, W)
            if p is None:
                continue
            paths.append(p)
            burst["burst(>=5)" if n >= 5 else "single"].append(p)
            if share == share and share_med is not None:
                techb["tech_hi" if share >= share_med else "tech_lo"].append(p)

    def summ(name, ps):
        if not ps:
            print(f"  {name:>12}: (no events)")
            return
        m = len(ps)
        mean_ar = [statistics.mean(col) for col in zip(*ps)]
        car, run = [], 0.0
        for a in mean_ar:
            run += a
            car.append(run)
        pre = car[W - 1] if W >= 1 else 0.0          # CAR[-W..-1]
        day0 = mean_ar[W]                            # AR on event day
        post = car[-1] - car[W]                      # CAR[+1..+W]
        post_per_event = [sum(p[W + 1:]) for p in ps]
        sd = statistics.pstdev(post_per_event) or 1e-9
        tstat = statistics.mean(post_per_event) / (sd / (m ** 0.5))
        ratios = []
        for p in ps:
            a = statistics.pstdev(p[:W]) if W >= 2 else 0.0
            b = statistics.pstdev(p[W + 1:]) if W >= 2 else 0.0
            if a > 1e-9:
                ratios.append(b / a)
        vr = statistics.median(ratios) if ratios else float("nan")
        print(f"  {name:>12}: n={m:>5}  AR(0)={day0:+.4f}  CAR[-W..-1]={pre:+.4f}  "
              f"CAR[+1..+W]={post:+.4f}  post t={tstat:+.2f}  vol_post/pre={vr:.3f}")

    print(f"\n[hiring event-study] window=+/-{W}td  benchmark={args.benchmark}  "
          f"events={len(paths)}  tech_share_median={share_med:.3f}\n"
          if share_med is not None else
          f"\n[hiring event-study] window=+/-{W}td  events={len(paths)}\n")
    print("CAR (AR = stock - benchmark):")
    for k in ("ALL",):
        summ(k, paths)
    summ("single", burst["single"])
    summ("burst(>=5)", burst["burst(>=5)"])
    summ("tech_hi", techb["tech_hi"])
    summ("tech_lo", techb["tech_lo"])

    # per-offset mean AR near the event to SEE the day-0 jump
    if paths:
        mean_ar = [statistics.mean(col) for col in zip(*paths)]
        near = range(max(0, W - 3), min(len(mean_ar), W + 4))
        print("\nmean AR by offset (t=0 is posting day):")
        print("  " + "  ".join(f"t{('%+d' % (k - W))}={mean_ar[k]:+.4f}" for k in near))
    print("\nRead: LEAD => CAR[-W..-1] !=0; COINCIDENT => AR(0) spikes & CAR[+1..+W]~0; "
          "LAG/predictive => CAR[+1..+W] clearly !=0 with |t|>~2.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
