#!/usr/bin/env python
"""Cross-sectional attention REVERSAL test on the KOSPI-15 universe (Stage 10, exp 1+2).

Every prior test was time-series on 3 stocks. The canonical attention effect
(Da/Engelberg/Gao 2011) is CROSS-SECTIONAL and a REVERSAL: stocks with abnormally
high retail attention get bid up by attention-driven buying, then UNDERPERFORM as
it reverses. A linear time-series corr (our Stage 8/9) averages that away. Here we
test it in its native form across 15 stocks.

Predictor (PIT, past-only): abnormal attention = trailing-26w rolling z of the
stock's weekly name-search level.

For each week t and horizon H (weeks), across all stocks with data that week:
  xs_excess_i = stock i's H-week return minus the cross-sectional MEAN return that week.
Tests:
  (1) cross-sectional IC = mean over weeks of corr(abn_attention_i, fwd xs_excess_i).
      NEGATIVE IC = reversal (high attention -> underperforms). t-stat on the weekly ICs.
  (2) portfolio sort: each week long bottom-attention / short top-attention tercile;
      mean forward xs_excess of (low - high). POSITIVE = reversal pays.
  (3) tail event study: extreme abnormal attention (z>2) -> mean forward xs_excess.
  (4) 16-MODEL BAKE-OFF on the cross-sectional dataset (rows=(stock,week),
      X=[abn, momentum, level], y=direction of fwd xs_excess) -> rankIC across all
      16 models with walk-forward (the same harness Stage 5 used).
"""
from __future__ import annotations

import argparse
import math
import statistics
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.bakeoff import run_bakeoff  # noqa: E402
from app.ml.period_keyword_dataset import load_keyword_series  # noqa: E402
from app.ml.prices_csv import load_prices_csv  # noqa: E402
from app.ml.report import render_table  # noqa: E402
from scripts.aggregate_search_momentum import name_index, weekly_closes_by_week  # noqa: E402
from scripts.event_study_leadlag import pearson, zscore  # noqa: E402
from scripts.search_target_change import _abnormal  # noqa: E402

HORIZONS = {1: "1주", 2: "2주", 4: "1개월"}


def load_all_search(csvs, tickers):
    """{ticker: {'level': {wk:z}, 'abn': {wk:rollz}, 'mom': {wk:1w change}}}."""
    merged = {}
    for c in csvs:
        merged.update(load_keyword_series(c))
    out = {}
    for t in tickers:
        raw = name_index(merged, t)  # {wk: ratio}
        if not raw:
            continue
        weeks = sorted(raw)
        zs = zscore([raw[w] for w in weeks])
        if zs is None:
            continue
        level = {w: z for w, z in zip(weeks, zs) if z is not None}
        abn = _abnormal(level)
        mom = {w: level[w] - level[w - 1] for w in level if (w - 1) in level}
        out[t] = {"level": level, "abn": abn, "mom": mom}
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="Cross-sectional attention reversal (KOSPI-15)")
    p.add_argument("--prices-csv", required=True)
    p.add_argument("--search-csvs", default="stockname_datalab.csv,stockname15_datalab.csv")
    p.add_argument("--tickers", required=True, help="comma-separated 15 tickers")
    args = p.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    prices = load_prices_csv(args.prices_csv)
    closes = {t: weekly_closes_by_week(prices[t]) for t in tickers if t in prices}
    search = load_all_search(args.search_csvs.split(","), tickers)
    common = [t for t in tickers if t in closes and t in search]
    print(f"universe: {len(common)} stocks with price+search\n")

    print(f"{'horizon':>8} {'weeks':>6} {'IC평균':>9} {'IC_t':>7} {'저-고_fwd%':>10} {'tail(z>2)_fwd%':>14}")
    for h in HORIZONS:
        weekly_ic, ptf, tail = [], [], []
        for t0 in sorted({w for tk in common for w in closes[tk]}):
            rets = {}
            for tk in common:
                c0, c1 = closes[tk].get(t0), closes[tk].get(t0 + h)
                if c0 and c1 and c0 > 0:
                    rets[tk] = (c1 / c0 - 1.0) * 100.0
            if len(rets) < 6:
                continue
            mean_r = statistics.mean(rets.values())
            xs = {tk: r - mean_r for tk, r in rets.items()}
            pairs = [(search[tk]["abn"][t0], xs[tk]) for tk in xs if t0 in search[tk]["abn"]]
            if len(pairs) < 6:
                continue
            ic = pearson([a for a, _ in pairs], [e for _, e in pairs])
            if ic is not None:
                weekly_ic.append(ic)
            sp = sorted(pairs, key=lambda x: x[0])
            k = max(1, len(sp) // 3)
            low = statistics.mean(e for _, e in sp[:k])
            high = statistics.mean(e for _, e in sp[-k:])
            ptf.append(low - high)  # reversal: low-attention beats high-attention
            tail += [e for a, e in pairs if a > 2.0]
        n = len(weekly_ic)
        ic_m = statistics.mean(weekly_ic) if weekly_ic else float("nan")
        ic_t = (ic_m / (statistics.pstdev(weekly_ic) / math.sqrt(n))
                if n > 2 and statistics.pstdev(weekly_ic) > 0 else float("nan"))
        print(f"{HORIZONS[h]:>8} {n:>6} {ic_m:>+9.3f} {ic_t:>+7.2f} "
              f"{(statistics.mean(ptf) if ptf else float('nan')):>+10.3f} "
              f"{(statistics.mean(tail) if tail else float('nan')):>+14.3f} (n_tail={len(tail)})")
    print("  Read: IC<0 & |t|>2 & 저-고>0 & tail<0  => 고관심 단기 반전(언더퍼폼). 모두 ≈0이면 무신호.\n")

    _bakeoff(common, closes, search, h=4)
    return 0


def _bakeoff(common, closes, search, *, h: int, band: float = 0.3):
    """16-model walk-forward on the cross-sectional attention dataset (direction of xs excess)."""
    X, y, exc, dates = [], [], [], []
    for t0 in sorted({w for tk in common for w in closes[tk]}):
        rets = {tk: (closes[tk][t0 + h] / closes[tk][t0] - 1.0) * 100.0
                for tk in common
                if closes[tk].get(t0) and closes[tk].get(t0 + h) and closes[tk][t0] > 0}
        if len(rets) < 6:
            continue
        mean_r = statistics.mean(rets.values())
        for tk in rets:
            s = search[tk]
            if t0 not in s["abn"]:
                continue
            xse = rets[tk] - mean_r
            if abs(xse) <= band:
                continue
            X.append([s["abn"][t0], s["mom"].get(t0, 0.0), s["level"].get(t0, 0.0)])
            y.append(1 if xse > 0 else 0)
            exc.append(xse)
            dates.append(t0)
    if len(y) < 80:
        print(f"[bakeoff] too few samples ({len(y)})")
        return
    reports = run_bakeoff(np.array(X, float), np.array(y, int),
                          np.array(exc, float), np.array(dates, int), n_folds=5)
    print(f"[16-MODEL BAKE-OFF | 횡단면 abnormal attention -> {HORIZONS[h]} 방향]  "
          f"samples={len(y)} up-rate={np.mean(y):.2f}")
    print(render_table(reports))


if __name__ == "__main__":
    raise SystemExit(main())
