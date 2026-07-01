#!/usr/bin/env python
"""SCRATCH (uncommitted) — audit gap ③: SUE-PEAD ECONOMICS + surprise-definition refinement.

search_pead_surprise.py established: PEAD is driven by the revenue SURPRISE (SUE→drift
decile +2.24%, perm p0.004), and search is a nowcaster (soaks into SUE, no independent
drift). Open question from the deep-research audit: is the SUE→drift decile actually
TRADEABLE after costs, or marginal (quarterly t<1.5, cost-fragile, 2021-23-dependent)?

This builds the missing economics:
  • per-ANNOUNCEMENT-QUARTER decile long-short (top-SUE − bottom-SUE), equal weight,
    held over the drift window → a time series of quarterly L-S returns.
  • gross mean, t across quarters, IR (=mean/std), hit-rate, annualized.
  • TRANSACTION COSTS: quarterly rebalance turns over both legs; sweep per-side cost
    (0/15/30/50 bps) → net t, breakeven cost. (KR round-trip ≈ commission+tax+½spread.)
  • decile monotonicity (mean drift by decile).
  • surprise-definition comparison: SUE_accel (growth-accel, current) vs SUE_yoy (raw
    revenue-YoY level as the sort key) vs search_abn (contrast — should be weaker).

Reuses search_pead / search_pead_surprise / search_to_fundamental loaders. Read-only,
local CSV/JSON only. No DB, no commits.

    PYTHONIOENCODING=utf-8 uv run python scripts/sue_economics.py \
        --disclosures dart_disclosures.json --search-csv stockname_daily_krx250.csv \
        --dart dart_krx250.csv --prices-csv prices_krx250.csv \
        --benchmark-csv prices_kospi15_2016_2026.csv --drift 20 --winsor=-0.9,3.0
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.prices_csv import load_prices_csv  # noqa: E402
from scripts.search_pead import earnings_dates, fwd_excess  # noqa: E402
from scripts.search_pead_surprise import revenue_sue  # noqa: E402
from scripts.search_to_fundamental import (  # noqa: E402
    load_revenue,
    quarter_search,
    rev_yoy,
    search_features,
)

SPLIT = 2021 * 4
COST_SIDES = [0.0, 0.0015, 0.0030, 0.0050]  # per-side fraction (0 / 15 / 30 / 50 bps)


def quarterly_ls(byq, k_frac=0.1, min_names=10):
    """byq: {q_ann: [(sortkey, drift%), ...]} → [(q_ann, LS%)] decile top−bottom per quarter."""
    out = []
    for q in sorted(byq):
        vals = [(k, d) for k, d in byq[q] if k is not None and d is not None]
        if len(vals) < min_names:
            continue
        vals.sort(key=lambda x: x[0])
        k = max(1, int(len(vals) * k_frac))
        bot = statistics.mean(d for _, d in vals[:k])
        top = statistics.mean(d for _, d in vals[-k:])
        out.append((q, top - bot))
    return out


def decile_profile(byq, nd=10, min_names=10):
    """Pooled mean drift by SUE decile (monotonicity check)."""
    buckets = defaultdict(list)
    for q in byq:
        vals = [(k, d) for k, d in byq[q] if k is not None and d is not None]
        if len(vals) < min_names:
            continue
        vals.sort(key=lambda x: x[0])
        n = len(vals)
        for i, (_, d) in enumerate(vals):
            buckets[min(nd - 1, i * nd // n)].append(d)
    return [statistics.mean(buckets[i]) if buckets[i] else float("nan") for i in range(nd)]


def econ(qrets):
    """Stats on a quarterly L-S return series."""
    ls = [v for _, v in qrets]
    n = len(ls)
    if n < 4:
        return None
    mean, sd = statistics.mean(ls), statistics.pstdev(ls)
    t = mean / (sd / math.sqrt(n)) if sd > 0 else float("nan")
    ir = mean / sd if sd > 0 else float("nan")
    hit = sum(1 for v in ls if v > 0) / n
    # era split
    e0 = [v for q, v in qrets if q < SPLIT]
    e1 = [v for q, v in qrets if q >= SPLIT]
    m0 = statistics.mean(e0) if e0 else float("nan")
    m1 = statistics.mean(e1) if e1 else float("nan")
    return {"n": n, "mean": mean, "sd": sd, "t": t, "ir": ir, "hit": hit,
            "ann": mean * 4, "m0": m0, "m1": m1}


def net_after_cost(st, c_side):
    """Quarterly rebalance: both legs enter+exit → 4×c_side per quarter (in %)."""
    cost_q = 4.0 * c_side * 100.0
    net_mean = st["mean"] - cost_q
    net_t = net_mean / (st["sd"] / math.sqrt(st["n"])) if st["sd"] > 0 else float("nan")
    return net_mean, net_t


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--disclosures", default="dart_disclosures.json")
    ap.add_argument("--search-csv", default="stockname_daily_krx250.csv")
    ap.add_argument("--dart", default="dart_krx250.csv")
    ap.add_argument("--prices-csv", default="prices_krx250.csv")
    ap.add_argument("--benchmark-csv", default="prices_kospi15_2016_2026.csv")
    ap.add_argument("--benchmark", default="KS11")
    ap.add_argument("--drift", type=int, default=20)
    ap.add_argument("--skip", type=int, default=1)
    ap.add_argument("--winsor", default="")
    args = ap.parse_args()

    w = None
    if args.winsor:
        lo, hi = (float(x) for x in args.winsor.split(","))
        w = (lo, hi)

    disclosures = json.loads(Path(args.disclosures).read_text(encoding="utf-8"))
    edates = earnings_dates(disclosures)
    abn, _ = search_features(quarter_search(args.search_csv))
    ry = rev_yoy(load_revenue(args.dart), winsor=w)
    sue = revenue_sue(ry)
    prices = load_prices_csv(args.prices_csv)
    bench = load_prices_csv(args.benchmark_csv).get(args.benchmark)
    if bench is None:
        raise SystemExit(f"benchmark {args.benchmark} not in {args.benchmark_csv}")

    # rows keyed by announcement quarter; sort keys = the three surprise variants
    keys = {"SUE_accel": defaultdict(list), "SUE_yoy": defaultdict(list),
            "search_abn": defaultdict(list)}
    matched = 0
    for t, ds in edates.items():
        if t not in prices:
            continue
        for d in ds:
            q_ann = d.year * 4 + (d.month - 1) // 3
            q_rev = q_ann - 1
            drift = fwd_excess(prices[t], bench, d, skip=args.skip, win=args.drift)
            if drift is None:
                continue
            matched += 1
            keys["SUE_accel"][q_ann].append((sue.get(t, {}).get(q_rev), drift))
            keys["SUE_yoy"][q_ann].append((ry.get(t, {}).get(q_rev), drift))
            keys["search_abn"][q_ann].append((abn.get(t, {}).get(q_rev), drift))

    print(f"실적공시 매칭 {matched}건 | drift[d+{args.skip},d+{args.skip+args.drift}] "
          f"| 분기 decile 롱숏(상위10%−하위10%, 동일가중)\n")

    print(f"  {'변수':>11} {'n분기':>5} {'gross%':>7} {'t':>6} {'IR':>6} {'hit':>5} "
          f"{'ann%':>6} {'16-20':>7} {'21-23':>7} | {'net_t@30bp':>10} {'BE(bps)':>8}")
    for name, byq in keys.items():
        qrets = quarterly_ls(byq)
        st = econ(qrets)
        if st is None:
            print(f"  {name:>11}  분기 부족")
            continue
        _, net_t30 = net_after_cost(st, 0.0030)
        be = st["mean"] / 4.0 / 100.0 * 1e4  # breakeven per-side in bps
        print(f"  {name:>11} {st['n']:>5} {st['mean']:>+7.2f} {st['t']:>+6.2f} "
              f"{st['ir']:>+6.2f} {st['hit']:>5.2f} {st['ann']:>+6.1f} "
              f"{st['m0']:>+7.2f} {st['m1']:>+7.2f} | {net_t30:>+10.2f} {be:>8.1f}")

    print("\n=== 비용 스윕 (SUE_accel 분기 롱숏) ===")
    st = econ(quarterly_ls(keys["SUE_accel"]))
    if st:
        print(f"  {'per-side':>9} {'net_mean%':>9} {'net_t':>7} {'net_ann%':>9}")
        for c in COST_SIDES:
            nm, nt = net_after_cost(st, c)
            print(f"  {int(c*1e4):>7}bp {nm:>+9.2f} {nt:>+7.2f} {nm*4:>+9.1f}")

    print("\n=== decile 단조성 (SUE_accel, 상위=고SUE, 평균 드리프트%) ===")
    prof = decile_profile(keys["SUE_accel"])
    print("  " + " ".join(f"D{i+1}:{v:+.2f}" for i, v in enumerate(prof)))

    print("\n판정선: gross t>2 & 비용후(30bp) net_t>2 & era 양쪽 양수 => 트레이더블 PEAD. "
          "비용후 소멸/era 편중 => 마진(비트레이딩, 나우캐스트로만).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
