#!/usr/bin/env python
"""SCRATCH — FEARS reversal TRADEABILITY under the ≥1-day publication lag.

scratch_fears_reversal.py found the FEARS→KOSPI reversal lives entirely in [t→t+1]
(close-to-close +17.7bps/1SD, k>=2 NULL). But FEARS_t (day-t search) is only known at
t+1. So the tradeable question is: of that +17.7bp, how much is the OVERNIGHT GAP
(close_t → open_{t+1}, NOT capturable if FEARS_t arrives around/after t+1 open) vs the
t+1 INTRADAY (open_{t+1} → close_{t+1}, capturable by entering at t+1 open)?

Decompose k=1 reversal with KS11 OHLC:
  gap_{t+1}      = open_{t+1}/close_t − 1
  intraday_{t+1} = close_{t+1}/open_{t+1} − 1
Regress standardized FEARS_t on each (+ total). Permutation on intraday. Net after a
KOSPI-ETF/futures round-trip cost. Same FEARS construction as scratch_fears_reversal.py
(train-era term selection, OOS test), read-only.

    PYTHONIOENCODING=utf-8 uv run python scripts/scratch_fears_tradeable.py \
        --fears-csv fears_terms.csv --ohlc-csv ks11_ohlc.csv
"""
from __future__ import annotations

import argparse
import csv
import math
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

random.seed(42)
NPERM = 2000
WIN = 60
TRAIN_END = "2020-01-01"
N_SELECT = 10
COST_BP = 5.0   # illustrative KOSPI ETF/futures round-trip (bps)


def load_terms(path):
    d = defaultdict(dict)
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                d[r["keyword"]][r["period"]] = float(r["ratio"])
            except (ValueError, KeyError):
                pass
    return d


def load_ohlc(path):
    o, c = {}, {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                o[r["date"]] = float(r["open"]); c[r["date"]] = float(r["close"])
            except (ValueError, KeyError):
                pass
    td = sorted(c)
    return td, o, c


def corr(xs, ys):
    n = len(xs)
    if n < 8:
        return float("nan")
    mx, my = statistics.mean(xs), statistics.mean(ys)
    sx = sum((x - mx) ** 2 for x in xs) ** 0.5
    sy = sum((y - my) ** 2 for y in ys) ** 0.5
    if sx == 0 or sy == 0:
        return float("nan")
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def slope_t(fears, rets):
    n = len(fears)
    if n < 20:
        return float("nan"), float("nan"), n
    r = corr(fears, rets)
    if r != r:
        return float("nan"), float("nan"), n
    slope_bps = r * statistics.pstdev(rets) * 100.0
    t = r * math.sqrt(n - 2) / math.sqrt(1 - r * r) if abs(r) < 1 else float("nan")
    return slope_bps, t, n


def term_shock(term_ratio, trading_days):
    vals = [(d, term_ratio.get(d)) for d in trading_days]
    out = {}
    for i, (d, v) in enumerate(vals):
        if v is None or i < WIN:
            continue
        hist = [vals[j][1] for j in range(i - WIN, i) if vals[j][1] is not None]
        if len(hist) < WIN // 2:
            continue
        mu, sd = statistics.mean(hist), statistics.pstdev(hist)
        if sd > 0:
            out[d] = (v - mu) / sd
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fears-csv", default="fears_terms.csv")
    ap.add_argument("--ohlc-csv", default="ks11_ohlc.csv")
    ap.add_argument("--n-select", type=int, default=N_SELECT)
    a = ap.parse_args()

    terms = load_terms(a.fears_csv)
    td, op, cl = load_ohlc(a.ohlc_csv)
    ret = {td[i]: (cl[td[i]] / cl[td[i - 1]] - 1) * 100 for i in range(1, len(td)) if cl[td[i - 1]] > 0}
    shocks = {k: term_shock(v, td) for k, v in terms.items()}
    shocks = {k: s for k, s in shocks.items() if len(s) > 200}

    # select fear terms on train era (neg corr with same-day return)
    train = [d for d in td if d < TRAIN_END and d in ret]
    stt = []
    for k, s in shocks.items():
        pr = [(s[d], ret[d]) for d in train if d in s]
        if len(pr) >= 100:
            stt.append((k, corr([x for x, _ in pr], [y for _, y in pr])))
    stt.sort(key=lambda x: (x[1] if x[1] == x[1] else 9))
    selected = [k for k, _ in stt[: a.n_select]]

    test = [d for d in td if d >= TRAIN_END]
    fears = {}
    for d in test:
        vv = [shocks[k][d] for k in selected if d in shocks[k]]
        if len(vv) >= max(3, len(selected) // 2):
            fears[d] = statistics.mean(vv)
    fv = list(fears.values())
    fmu, fsd = statistics.mean(fv), (statistics.pstdev(fv) or 1.0)
    fz = {d: (v - fmu) / fsd for d, v in fears.items()}
    idx = {d: i for i, d in enumerate(td)}
    print(f"OOS test days {len(fz)} · FEARS terms {len(selected)}\n")

    # decompose k=1 reversal into overnight gap + t+1 intraday
    rows = {"k1_total (close_t→close_{t+1})": [], "overnight gap (close_t→open_{t+1})": [],
            "t+1 intraday (open→close, TRADEABLE)": []}
    for d, z in fz.items():
        i = idx[d]
        if i + 1 >= len(td):
            continue
        d1 = td[i + 1]
        if cl[d] <= 0 or op.get(d1) is None:
            continue
        gap = (op[d1] / cl[d] - 1) * 100
        intra = (cl[d1] / op[d1] - 1) * 100
        tot = (cl[d1] / cl[d] - 1) * 100
        rows["k1_total (close_t→close_{t+1})"].append((z, tot))
        rows["overnight gap (close_t→open_{t+1})"].append((z, gap))
        rows["t+1 intraday (open→close, TRADEABLE)"].append((z, intra))

    print("=== FEARS_t → k=1 반전 분해 (지연 후 진입 가능성) ===")
    print(f"  {'component':>42} {'slope(bps/1SD)':>15} {'t':>7} {'n':>6}")
    for name, pairs in rows.items():
        xs = [x for x, _ in pairs]; ys = [y for _, y in pairs]
        sl, t, n = slope_t(xs, ys)
        print(f"  {name:>42} {sl:>+15.2f} {t:>+7.2f} {n:>6}")

    # permutation + net-of-cost on the tradeable (intraday) leg
    pairs = rows["t+1 intraday (open→close, TRADEABLE)"]
    xs = [x for x, _ in pairs]; ys = [y for _, y in pairs]
    obs = corr(xs, ys)
    ysh = ys[:]; hits = 0
    for _ in range(NPERM):
        random.shuffle(ysh)
        if abs(corr(xs, ysh)) >= abs(obs):
            hits += 1
    pp = (hits + 1) / (NPERM + 1)
    sl, t, n = slope_t(xs, ys)
    net = sl - COST_BP
    print(f"\n  [트레이더블 leg = t+1 intraday] slope={sl:+.2f}bps/1SD t={t:+.2f} perm_p={pp:.4f}")
    print(f"  비용후(왕복 {COST_BP:.0f}bp 가정): net={net:+.2f}bps/1SD  (>0 & perm<0.05 여야 실사용)")
    print("\n판정선: intraday(진입가능) leg이 유의+비용후 양수 => FEARS 실트레이더블. "
          "반전이 overnight gap에 몰려있으면(intraday NULL) => 지표로만 유효, 트레이딩 불가.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
