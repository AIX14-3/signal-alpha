#!/usr/bin/env python
"""PEAD deepened: define a revenue SURPRISE (SUE) and decompose the chain.

search_pead.py found pre-announcement search weakly predicts the 20-40d post-earnings
drift (+0.035). PEAD is properly driven by the earnings SURPRISE — the part of results
the market did NOT expect. With no analyst consensus on disk we use a time-series SUE:
the reported quarter's revenue-YoY standardized vs the firm's own trailing-4q mean/std
(growth acceleration vs its recent trend). We then decompose:

  T1  SUE(Q_rev)        → drift     does PEAD exist (surprise drifts)?
  T2  search_abn(Q_rev) → SUE       does search capture the unexpected component?
  T3  search_abn        → drift | SUE   does search add BEYOND the surprise?

Reported quarter Q_rev = q_ann − 1 (KR 잠정실적 reports the quarter that ended ~45d before
the announcement: Feb→prior-Q4, May→Q1, Aug→Q2, Nov→Q3). Cross-sectional IC per
announcement-quarter + within-quarter permutation + era split + decile spread (economics).

    uv run python scripts/search_pead_surprise.py --disclosures dart_disclosures.json \
        --search-csv stockname_daily_krx250.csv --dart dart_krx250.csv \
        --prices-csv prices_krx250.csv --benchmark-csv prices_kospi15_2016_2026.csv \
        --drift 20 --winsor=-0.9,3.0
"""
from __future__ import annotations

import argparse
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.search_pead import earnings_dates, fwd_excess, xs_ic_perm  # noqa: E402
from scripts.search_to_fundamental import (  # noqa: E402
    load_revenue,
    quarter_search,
    rev_yoy,
    search_features,
)

import json  # noqa: E402

from app.ml.prices_csv import load_prices_csv  # noqa: E402


def revenue_sue(ry, *, trail=4, min_n=3):
    """{ticker: {qi: SUE}} = (rev_yoy(qi) − trailing mean) / trailing std (PIT, firm-level)."""
    out = defaultdict(dict)
    for t, qd in ry.items():
        for qi, v in qd.items():
            hist = [qd[h] for h in range(qi - trail, qi) if h in qd]
            if len(hist) >= min_n:
                mu = statistics.mean(hist)
                sd = statistics.pstdev(hist)
                if sd > 0:
                    out[t][qi] = (v - mu) / sd
    return out


def _ols_resid(xs, ys):
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx > 0 else 0.0
    a = my - b * mx
    return [y - (a + b * x) for x, y in zip(xs, ys)]


def decile_spread(pairs):
    """Top-decile minus bottom-decile mean target, pooled (economic magnitude, % drift)."""
    if len(pairs) < 20:
        return float("nan")
    ordered = sorted(pairs, key=lambda p: p[0])
    k = max(1, len(ordered) // 10)
    bottom = statistics.mean(t for _, t in ordered[:k])
    top = statistics.mean(t for _, t in ordered[-k:])
    return top - bottom


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
    ap.add_argument("--nperm", type=int, default=2000)
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

    SPLIT = 2021 * 4
    # Build per-announcement rows keyed by reported quarter Q_rev = q_ann − 1.
    rows = []  # (q_ann, search_abn, sue, drift)
    for t, ds in edates.items():
        if t not in prices:
            continue
        for d in ds:
            q_ann = d.year * 4 + (d.month - 1) // 3
            q_rev = q_ann - 1
            sa = abn.get(t, {}).get(q_rev)
            su = sue.get(t, {}).get(q_rev)
            drift = fwd_excess(prices[t], bench, d, skip=args.skip, win=args.drift)
            if drift is None:
                continue
            rows.append((q_ann, sa, su, drift))

    n_sa = sum(1 for r in rows if r[1] is not None)
    n_su = sum(1 for r in rows if r[2] is not None)
    print(f"실적공시 매칭 {len(rows)}건 | search_abn {n_sa} · SUE {n_su} | "
          f"drift [d+{args.skip},d+{args.skip+args.drift}] | NPERM={args.nperm}\n")

    def run(name, xsel, ysel, control=None):
        """Cross-sectional IC per q_ann of xsel→ysel (optionally residualized on control)."""
        byq, pooled_pairs = defaultdict(list), []
        for q_ann, sa, su, drift in rows:
            x = xsel(sa, su)
            y = ysel(sa, su, drift)
            c = control(sa, su) if control else None
            if x is None or y is None or (control and c is None):
                continue
            byq[q_ann].append((x, y, c))
            pooled_pairs.append((x, y))
        # residualize within quarter if control given
        clean = defaultdict(list)
        for q, rws in byq.items():
            if control:
                xs = _ols_resid([r[2] for r in rws], [r[0] for r in rws])
                ys = _ols_resid([r[2] for r in rws], [r[1] for r in rws])
                clean[q] = list(zip(xs, ys))
            else:
                clean[q] = [(r[0], r[1]) for r in rws]
        obs, p2, nq = xs_ic_perm(clean, args.nperm)
        e0 = xs_ic_perm({q: v for q, v in clean.items() if q < SPLIT}, 1)[0]
        e1 = xs_ic_perm({q: v for q, v in clean.items() if q >= SPLIT}, 1)[0]
        ds = decile_spread(pooled_pairs)
        print(f"  {name:>34} {obs:>+8.3f} {p2:>8.4f} {nq:>5}   {e0:>+8.3f} {e1:>+8.3f}   {ds:>+7.2f}")
        return obs, p2

    print(f"  {'cell':>34} {'IC평균':>8} {'perm_p':>8} {'분기':>5}   {'16-20':>8} {'21-23':>8}   {'decile%':>7}")
    run("T1 SUE→drift (PEAD?)", lambda sa, su: su, lambda sa, su, dr: dr)
    run("T2 search→SUE", lambda sa, su: sa, lambda sa, su, dr: su)
    run("T3a search→drift (raw)", lambda sa, su: sa, lambda sa, su, dr: dr)
    run("T3b search→drift | SUE", lambda sa, su: sa, lambda sa, su, dr: dr,
        control=lambda sa, su: su)
    run("T1b SUE→drift | search", lambda sa, su: su, lambda sa, su, dr: dr,
        control=lambda sa, su: sa)

    print("\n  T1 PEAD 존재? · T2 검색이 서프라이즈 포착? · T3b 검색이 서프라이즈 너머 드리프트 추가?")
    print("  decile% = 상위10% − 하위10% 평균 드리프트(경제적 크기). era 일관·perm_p<0.05면 신호.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
