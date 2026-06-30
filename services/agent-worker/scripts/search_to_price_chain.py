#!/usr/bin/env python
"""Chain completion: search → revenue (confirmed) → PRICE?

R established search abn (quarter Q) cross-sectionally LEADS next-quarter revenue YoY
(219 stocks, permutation+BH-FDR robust). The product question is whether that
revenue-predictive search translates to PRICE, or whether revenue is already
anticipated/priced (which would explain the flat direction tests).

We reuse search_to_fundamental's quarter machinery (qi = year*4+(q-1), search_features,
rev_yoy, xs_ic, pooled) and add a QUARTERLY market-adjusted PRICE-return target:
quarter-end close ratio minus KS11's, per (ticker, qi). Then cross-sectional IC of

  C1  search_abn(Q)  → price_excess(Q+lag)     full chain (search→price)
  C2  revenue_yoy(Q) → price_excess(Q+lag)     is revenue growth priced?
  C3  search_abn(Q)  → price_excess, control=revenue_yoy(Q)   does search ADD beyond revenue?

judged honestly with a within-quarter label-shuffle permutation (NPERM) + BH-FDR
across the cell family — the same bar R cleared.

    uv run python scripts/search_to_price_chain.py --dart dart_krx250.csv \
        --search-csv stockname_daily_krx250.csv --prices-csv prices_krx250.csv \
        --benchmark-csv prices_kospi15_2016_2026.csv --benchmark KS11 --winsor=-0.9,3.0
"""
from __future__ import annotations

import argparse
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.prices_csv import load_prices_csv  # noqa: E402
from scripts.event_study_leadlag import pearson  # noqa: E402
from scripts.search_to_fundamental import (  # noqa: E402
    load_revenue,
    pooled,
    quarter_search,
    rev_yoy,
    search_features,
    xs_ic,
)

random.seed(42)


def quarter_end_close(ps):
    """{qi: last close in calendar quarter qi} for one PriceSeries."""
    out = {}
    for d, c in zip(ps.dates, ps.closes):
        if c > 0:
            out[d.year * 4 + (d.month - 1) // 3] = c  # dates ascending → keeps quarter's last
    return out


def quarter_price_excess(prices, bench):
    """{ticker: {qi: market-adjusted quarter return}} = stock qret − benchmark qret."""
    bq = quarter_end_close(bench)
    out = defaultdict(dict)
    for t, ps in prices.items():
        qc = quarter_end_close(ps)
        for qi in qc:
            if (qi - 1) in qc and qc[qi - 1] > 0 and (qi - 1) in bq and qi in bq and bq[qi - 1] > 0:
                sret = qc[qi] / qc[qi - 1] - 1.0
                bret = bq[qi] / bq[qi - 1] - 1.0
                out[t][qi] = sret - bret
    return out


def _ols_resid(xs, ys):
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    b = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx if sxx > 0 else 0.0
    a = my - b * mx
    return [y - (a + b * x) for x, y in zip(xs, ys)]


def panels(pred, targ, lag, control=None):
    """Per quarter: (pred, fwd target) vectors; if control given, residualize both on it."""
    byq = defaultdict(list)
    for t in pred:
        if t not in targ:
            continue
        for qi, pv in pred[t].items():
            tv = targ[t].get(qi + lag)
            cv = control[t].get(qi) if control is not None and t in control else None
            if tv is None or (control is not None and cv is None):
                continue
            byq[qi].append((pv, tv, cv))
    out = {}
    for qi, rows in byq.items():
        if len(rows) < 6:
            continue
        s = [r[0] for r in rows]
        f = [r[1] for r in rows]
        if control is not None:
            c = [r[2] for r in rows]
            s, f = _ols_resid(c, s), _ols_resid(c, f)
        out[qi] = (s, f)
    return out


def mean_ic(pn, fperm=None):
    ics = [pearson(s, f if fperm is None else fperm[qi]) for qi, (s, f) in pn.items()]
    ics = [x for x in ics if x is not None]
    return statistics.mean(ics) if ics else float("nan")


def perm_p(pn, obs, nperm):
    two = 0
    for _ in range(nperm):
        fperm = {}
        for qi, (s, f) in pn.items():
            ff = f[:]
            random.shuffle(ff)
            fperm[qi] = ff
        if abs(mean_ic(pn, fperm)) >= abs(obs):
            two += 1
    return (two + 1) / (nperm + 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dart", default="dart_krx250.csv")
    ap.add_argument("--search-csv", default="stockname_daily_krx250.csv")
    ap.add_argument("--prices-csv", default="prices_krx250.csv")
    ap.add_argument("--benchmark-csv", default="prices_kospi15_2016_2026.csv")
    ap.add_argument("--benchmark", default="KS11")
    ap.add_argument("--winsor", default="")
    ap.add_argument("--nperm", type=int, default=2000)
    args = ap.parse_args()

    w = None
    if args.winsor:
        lo, hi = (float(x) for x in args.winsor.split(","))
        w = (lo, hi)
    abn, _syoy = search_features(quarter_search(args.search_csv))
    ry = rev_yoy(load_revenue(args.dart), winsor=w)
    prices = load_prices_csv(args.prices_csv)
    bench = load_prices_csv(args.benchmark_csv).get(args.benchmark)
    if bench is None:
        raise SystemExit(f"benchmark {args.benchmark} not in {args.benchmark_csv}")
    px = quarter_price_excess(prices, bench)

    npx = sum(len(v) for v in px.values())
    print(f"분기 price-excess 관측치 {npx} (종목 {sum(1 for v in px.values() if v)})\n")

    # Reference: revenue YoY → price (is realized growth priced?), and search → revenue (R recap).
    print("=== 분기 price-excess 횡단면 IC (lag 스윕) ===")
    print(f"  {'cell':>34} {'IC평균':>8} {'IC_t':>7} {'분기수':>6}")
    cells = []  # (label, pred, target, lag, control)
    for lag in (0, 1, 2):
        cells.append((f"C1 search_abn→price (lag{lag})", abn, px, lag, None))
    for lag in (0, 1):
        cells.append((f"C2 revenue_yoy→price (lag{lag})", ry, px, lag, None))
    cells.append(("C3 search_abn→price | rev (lag1)", abn, px, 1, ry))
    # sanity recap: search→revenue (should reproduce R's +0.05)
    cells.append(("R  search_abn→revenue (lag1)", abn, ry, 1, None))

    rows = []
    for label, pred, targ, lag, ctrl in cells:
        m, t, n = xs_ic(pred, targ, lag) if ctrl is None else (None, None, None)
        pn = panels(pred, targ, lag, ctrl)
        obs = mean_ic(pn)
        if ctrl is not None:  # xs_ic has no control path; report the panel mean IC
            m, n = obs, len(pn)
            t = float("nan")
        p2 = perm_p(pn, obs, args.nperm) if pn else float("nan")
        rows.append([label, m, t, n, obs, p2])
        print(f"  {label:>34} {m:>+8.3f} {(t if t==t else 0):>+7.2f} {n:>6}")

    # BH-FDR across the permutation p-values (the honest multiple-test family).
    m_fam = len(rows)
    order = sorted(range(m_fam), key=lambda i: rows[i][5])
    alpha = 0.05
    k_star = 0
    for rank, idx in enumerate(order, 1):
        if rows[idx][5] <= (rank / m_fam) * alpha:
            k_star = rank
    qv = [1.0] * m_fam
    run = 1.0
    for rank in range(m_fam, 0, -1):
        idx = order[rank - 1]
        run = min(run, m_fam / rank * rows[idx][5])
        qv[idx] = run
    rank_of = {idx: r for r, idx in enumerate(order, 1)}

    print(f"\n=== permutation(NPERM={args.nperm}) + BH-FDR (m={m_fam}) ===")
    print(f"  {'cell':>34} {'obs_IC':>8} {'perm_p':>8} {'BH_q':>7} {'rej':>4}")
    for i in order:
        label, _m, _t, _n, obs, p2 = rows[i]
        rej = "YES" if rank_of[i] <= k_star else ""
        print(f"  {label:>34} {obs:>+8.3f} {p2:>8.4f} {qv[i]:>7.3f} {rej:>4}")
    print(f"\n  BH survivors (q<=0.05): {k_star} / {m_fam}")
    print("  C1 search→price = full chain; C2 revenue→price = is growth priced; "
          "C3 = search beyond revenue. R recap should reproduce ~+0.05.")
    # pooled firm-level lead (context)
    pr, pt, pn_ = pooled(abn, px, 1)
    print(f"  [pooled firm-level] search_abn→price lag1 r={pr:+.3f} t={pt:+.2f} n={pn_}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
