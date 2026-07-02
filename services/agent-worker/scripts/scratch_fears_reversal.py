#!/usr/bin/env python
"""SCRATCH (uncommitted) — audit gap ② (fusion-adjacent): FEARS-style aggregate search
sentiment → short-term KOSPI REVERSAL (Da-Engelberg-Gao 2015).

Not a per-stock signal — a MARKET-LEVEL risk-off / reversal overlay. DEG: an index of
household economic-concern search terms (recession/unemployment/…) is a signed, negatively-
valenced sentiment gauge; a spike coincides with LOW same-day market return then predicts a
small POSITIVE reversal over the next 1-2 days (~+7bp/day), fading by day 3.

Construction (honest, no look-ahead in selection):
  term shock_t = PIT rolling-z (trailing 60 trading days) of the term's daily search ratio.
  SELECT (train era) the terms whose shock is most NEGATIVELY correlated with same-day KOSPI
    return (= genuine "fear" terms). Build FEARS_t = mean shock of selected terms.
  TEST (out-of-sample era): regress KOSPI ret_{t+k} on standardized FEARS_t, k=0..5.
    Reversal signature: k=0 negative, k=1/2 positive, k>=3 ~0. Permutation on the test era.

Read-only, local CSVs (fears_terms.csv from collect_datalab_terms.py + KS11). No commits.

    PYTHONIOENCODING=utf-8 uv run python scripts/scratch_fears_reversal.py \
        --fears-csv fears_terms.csv --benchmark-csv prices_kospi15_2016_2026.csv
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
WIN = 60           # trailing trading days for term-shock rolling z
TRAIN_END = "2020-01-01"   # select terms on < TRAIN_END, test on >=
N_SELECT = 10      # # of most-negatively-correlated terms to form FEARS


def load_terms(path):
    d = defaultdict(dict)
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                d[r["keyword"]][r["period"]] = float(r["ratio"])
            except (ValueError, KeyError):
                pass
    return d


def load_kospi(path, ticker):
    close = {}
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            if r["ticker"] == ticker:
                try:
                    close[r["date"]] = float(r["close"])
                except (ValueError, KeyError):
                    pass
    td = sorted(close)
    ret = {}
    for i in range(1, len(td)):
        c0, c1 = close[td[i - 1]], close[td[i]]
        if c0 > 0:
            ret[td[i]] = (c1 / c0 - 1.0) * 100.0
    return td, ret


def term_shock(term_ratio, trading_days):
    """PIT rolling-z of the term's ratio on trading days (trailing WIN prior days)."""
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
    """OLS slope of rets on standardized fears (bps per 1-SD) + t via correlation."""
    n = len(fears)
    if n < 20:
        return float("nan"), float("nan"), n
    r = corr(fears, rets)
    if r != r:
        return float("nan"), float("nan"), n
    sd_r = statistics.pstdev(rets)
    slope_bps = r * sd_r * 100.0   # per 1-SD FEARS, in bps (rets already in %)
    t = r * math.sqrt(n - 2) / math.sqrt(1 - r * r) if abs(r) < 1 else float("nan")
    return slope_bps, t, n


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fears-csv", default="fears_terms.csv")
    ap.add_argument("--benchmark-csv", default="prices_kospi15_2016_2026.csv")
    ap.add_argument("--benchmark", default="KS11")
    ap.add_argument("--n-select", type=int, default=N_SELECT)
    a = ap.parse_args()

    terms = load_terms(a.fears_csv)
    td, ret = load_kospi(a.benchmark_csv, a.benchmark)
    tdset = set(td)
    shocks = {k: term_shock(v, td) for k, v in terms.items()}
    shocks = {k: s for k, s in shocks.items() if len(s) > 200}
    print(f"terms {len(shocks)} · KOSPI 거래일 {len(td)} · train<{TRAIN_END} · WIN={WIN}\n")

    # --- SELECT on train era: terms whose shock is most negatively corr with same-day return
    train_days = [d for d in td if d < TRAIN_END and d in ret]
    sel_stats = []
    for k, s in shocks.items():
        pairs = [(s[d], ret[d]) for d in train_days if d in s]
        if len(pairs) < 100:
            continue
        c = corr([x for x, _ in pairs], [y for _, y in pairs])
        sel_stats.append((k, c, len(pairs)))
    sel_stats.sort(key=lambda x: (x[1] if x[1] == x[1] else 9))  # most negative first
    selected = [k for k, c, _ in sel_stats[: a.n_select]]
    print("=== 선별(train기 시장수익과 음의상관 top) — FEARS 구성 term ===")
    for k, c, n in sel_stats[: a.n_select]:
        print(f"  {k:>10}  train_corr={c:+.3f}  (n={n})")
    print()

    def fears_on(days):
        out = {}
        for d in days:
            vv = [shocks[k][d] for k in selected if d in shocks[k]]
            if len(vv) >= max(3, len(selected) // 2):
                out[d] = statistics.mean(vv)
        return out

    # --- TEST era (out-of-sample): FEARS_t vs ret_{t+k}
    test_days = [d for d in td if d >= TRAIN_END]
    fears = fears_on(test_days)
    # standardize FEARS over test era
    fv = list(fears.values())
    fmu, fsd = statistics.mean(fv), (statistics.pstdev(fv) or 1.0)
    fz = {d: (v - fmu) / fsd for d, v in fears.items()}
    idx = {d: i for i, d in enumerate(td)}

    def aligned(k):
        xs, ys = [], []
        for d, fzv in fz.items():
            j = idx[d] + k
            if j < len(td) and td[j] in ret:
                xs.append(fzv); ys.append(ret[td[j]])
        return xs, ys

    print("=== OOS 검정: FEARS_t → KOSPI ret_{t+k} (반전=k0 음, k1/k2 양) ===")
    print(f"  {'k(일)':>5} {'slope(bps/1SD)':>15} {'t':>7} {'n':>6}")
    cum = None
    for k in range(0, 6):
        xs, ys = aligned(k)
        sl, t, n = slope_t(xs, ys)
        print(f"  {k:>5} {sl:>+15.2f} {t:>+7.2f} {n:>6}")

    # cumulative k=1..2 + permutation
    def cum_ret(k1, k2):
        xs, ys = [], []
        for d, fzv in fz.items():
            j = idx[d]
            r = 0.0; ok = True
            for kk in range(k1, k2 + 1):
                if j + kk < len(td) and td[j + kk] in ret:
                    r += ret[td[j + kk]]
                else:
                    ok = False; break
            if ok:
                xs.append(fzv); ys.append(r)
        return xs, ys

    xs, ys = cum_ret(1, 2)
    sl, t, n = slope_t(xs, ys)
    # permutation: shuffle FEARS labels vs the k1-2 cumulative return
    obs = corr(xs, ys)
    hits = 0
    ysh = ys[:]
    for _ in range(NPERM):
        random.shuffle(ysh)
        if abs(corr(xs, ysh)) >= abs(obs):
            hits += 1
    pp = (hits + 1) / (NPERM + 1)
    print(f"\n  누적 k=1..2: slope={sl:+.2f}bps/1SD  t={t:+.2f}  perm_p={pp:.4f}  n={n}")

    # diagnostic: full-sample (in-sample selection) contemporaneous k0 to sanity-check sign
    print("\n[진단] 전체기간 in-sample k0 (부호 확인용, look-ahead 있음):")
    allf = fears_on([d for d in td if d in ret])
    av = list(allf.values()); amu, asd = statistics.mean(av), (statistics.pstdev(av) or 1.0)
    afz = {d: (v - amu) / asd for d, v in allf.items()}
    xs = [afz[d] for d in allf if d in ret]; ys = [ret[d] for d in allf if d in ret]
    sl, t, n = slope_t(xs, ys)
    print(f"  k0 slope={sl:+.2f}bps/1SD t={t:+.2f} n={n} (음수여야 FEARS=공포 정합)")

    print("\n판정선: OOS k0<0 & k1/k2>0 & 누적 perm_p<0.05 => FEARS 단기 반전 신호(시장 타이밍 오버레이). "
          "아니면 무신호. (효과 ~15bp·비용취약 유의)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
