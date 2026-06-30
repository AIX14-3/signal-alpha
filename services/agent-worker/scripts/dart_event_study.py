"""DART event-study: does a disclosure EVENT itself move the stock (vs the market),
BYPASSING DataLab search entirely?

For each market-moving disclosure TYPE (유상증자·자사주매입·공급계약·소송…) we align all
its events at day 0 (rcept_dt) and measure the forward MARKET-ADJUSTED return (stock
return − KS11 return over the same window) at horizons h. Significance is judged
honestly: a within-ticker date-shuffle permutation null (NPERM) breaks the event→return
link while preserving each stock's own return distribution, then BH-FDR across the
event-type family. PIT: rcept_dt is the public date; forward returns are strictly after.

Reuses: EVENT_TYPES taxonomy from gen_dart_event_keywords, prices via app.ml.prices_csv,
the day-0 CAR idea from build_event_study.py, the permutation+BH block from
scratch_*_permutation_fdr.py. No DataLab, no DB, no commits of data.

    uv run python scripts/dart_event_study.py --disclosures dart_disclosures.json \
        --prices-csv prices_krx250.csv --benchmark-csv prices_kospi15_2016_2026.csv \
        --benchmark KS11 --horizons 5,10,20
"""

from __future__ import annotations

import argparse
import bisect
import json
import random
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.prices_csv import load_prices_csv  # noqa: E402
from scripts.gen_dart_event_keywords import EVENT_TYPES  # noqa: E402

random.seed(42)


def classify(title: str) -> list[str]:
    """All event-type labels whose substring patterns appear in a disclosure title."""
    out = []
    for patterns, issue in EVENT_TYPES:
        if any(p in title for p in patterns):
            out.append(issue)
    return out


def build_events(disclosures: dict) -> list[tuple[str, str, str]]:
    """(ticker, event_date ISO, event_type) for every classified disclosure (deduped)."""
    seen: set[tuple[str, str, str]] = set()
    events = []
    for ticker, rows in disclosures.items():
        for r in rows:
            rcept = (r.get("rcept_dt") or "")[:8]
            title = r.get("report_nm") or ""
            if len(rcept) < 8:
                continue
            iso = f"{rcept[:4]}-{rcept[4:6]}-{rcept[6:8]}"
            for issue in classify(title):
                key = (ticker, iso, issue)
                if key not in seen:
                    seen.add(key)
                    events.append(key)
    return events


def abn_forward(ps, bench, h):
    """{trading_index i: market-adjusted forward return %} for a ticker at horizon h.

    abn = (close[i+h]/close[i]-1) - (KS11[date_i+h]/KS11[date_i]-1), in %. Only indices
    whose entry AND exit dates both exist in the benchmark are kept.
    """
    out = {}
    n = len(ps.closes)
    for i in range(n - h):
        c0, c1 = ps.closes[i], ps.closes[i + h]
        if c0 <= 0 or c1 <= 0:
            continue
        d0, d1 = ps.dates[i], ps.dates[i + h]
        b0 = bench._index.get(d0)
        b1 = bench._index.get(d1)
        if b0 is None or b1 is None:
            continue
        bc0, bc1 = bench.closes[b0], bench.closes[b1]
        if bc0 <= 0 or bc1 <= 0:
            continue
        out[i] = ((c1 / c0 - 1.0) - (bc1 / bc0 - 1.0)) * 100.0
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--disclosures", default="dart_disclosures.json")
    ap.add_argument("--prices-csv", default="prices_krx250.csv")
    ap.add_argument("--benchmark-csv", default="prices_kospi15_2016_2026.csv")
    ap.add_argument("--benchmark", default="KS11")
    ap.add_argument("--horizons", default="5,10,20")
    ap.add_argument("--nperm", type=int, default=2000)
    ap.add_argument("--min-events", type=int, default=6)
    args = ap.parse_args()

    horizons = [int(h) for h in args.horizons.split(",") if h.strip()]
    disclosures = json.loads(Path(args.disclosures).read_text(encoding="utf-8"))
    prices = load_prices_csv(args.prices_csv)
    bench_prices = load_prices_csv(args.benchmark_csv)
    bench = bench_prices.get(args.benchmark)
    if bench is None:
        raise SystemExit(f"benchmark {args.benchmark} not in {args.benchmark_csv}")

    events = build_events(disclosures)
    tickers = sorted({t for t, _, _ in events if t in prices})

    # Precompute per-ticker abn-forward maps and the sorted trading-day list (for entry lookup).
    abn_by_tkr = {h: {} for h in horizons}
    td_by_tkr = {}
    for t in tickers:
        ps = prices[t]
        td_by_tkr[t] = ps.dates
        for h in horizons:
            abn_by_tkr[h][t] = abn_forward(ps, bench, h)

    def entry_index(t, iso):
        """First trading-day index on/after the event date (rcept_dt)."""
        td = td_by_tkr[t]
        from datetime import date
        ed = date.fromisoformat(iso)
        j = bisect.bisect_left(td, ed)
        return j if j < len(td) else None

    # Collect, per (type, h), the list of (ticker, abn) at event entry indices.
    cells = {}  # (issue, h) -> list[(ticker, abn)]
    for t, iso, issue in events:
        if t not in prices:
            continue
        i = entry_index(t, iso)
        if i is None:
            continue
        for h in horizons:
            a = abn_by_tkr[h][t].get(i)
            if a is not None:
                cells.setdefault((issue, h), []).append((t, a))

    # Observed mean + within-ticker date-shuffle permutation p per cell.
    valid_idx = {h: {t: list(abn_by_tkr[h][t].keys()) for t in tickers} for h in horizons}
    results = []
    for (issue, h), rows in cells.items():
        n = len(rows)
        if n < args.min_events:
            results.append([issue, h, statistics.mean(a for _, a in rows) if rows else float("nan"),
                            float("nan"), n, "underpowered"])
            continue
        obs = statistics.mean(a for _, a in rows)
        ge = 0
        for _ in range(args.nperm):
            tot = 0.0
            for t, _a in rows:
                idxs = valid_idx[h][t]
                i2 = random.choice(idxs)
                tot += abn_by_tkr[h][t][i2]
            if abs(tot / n) >= abs(obs):
                ge += 1
        p2 = (ge + 1) / (args.nperm + 1)
        results.append([issue, h, obs, p2, n, ""])

    # BH-FDR across the full family (testable cells only).
    testable = [r for r in results if r[5] != "underpowered"]
    m = len(testable)
    order = sorted(range(m), key=lambda i: testable[i][3])
    alpha = 0.05
    k_star = 0
    for rank, idx in enumerate(order, 1):
        if testable[idx][3] <= (rank / m) * alpha:
            k_star = rank
    qv = {id(testable[i]): 1.0 for i in range(m)}
    run = 1.0
    for rank in range(m, 0, -1):
        idx = order[rank - 1]
        run = min(run, m / rank * testable[idx][3])
        qv[id(testable[idx])] = run
    rank_of = {id(testable[idx]): r for r, idx in enumerate(order, 1)}

    print(f"DART event-study | {len(events)} events, {len(tickers)} tickers | "
          f"family m={m} cells (types×h) | NPERM={args.nperm} | BH alpha=0.05")
    print(f"  Bonferroni ref 0.05/{m}={0.05/m:.4f} | perm p-floor {1/(args.nperm+1):.4f}\n")
    print(f"  {'event_type':>14} {'h':>3} {'abnCAR%':>9} {'p_2side':>8} {'BH_q':>7} {'rej':>4} {'n':>5}")
    for r in sorted(results, key=lambda x: (x[1], -(abs(x[2]) if x[2] == x[2] else 0))):
        issue, h, obs, p2, n, flag = r
        if flag == "underpowered":
            print(f"  {issue:>14} {h:>3} {obs:>+9.2f} {'n/a':>8} {'n/a':>7} {'·':>4} {n:>5}  (underpowered)")
        else:
            q = qv[id(r)]
            rej = "YES" if rank_of[id(r)] <= k_star else ""
            print(f"  {issue:>14} {h:>3} {obs:>+9.2f} {p2:>8.4f} {q:>7.3f} {rej:>4} {n:>5}")
    print(f"\n  BH survivors (q<=0.05): {k_star} / {m}")
    print("  abnCAR% = mean (stock − KS11) forward return; 2-sided perm p = event timing vs random.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
