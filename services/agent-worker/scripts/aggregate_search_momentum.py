#!/usr/bin/env python
"""Stock-level AGGREGATE search-interest momentum vs returns, multi-horizon (Exp 2).

Earlier tests were per-keyword and event-anchored. This asks a different question:
build ONE composite "how much is this stock being searched" index per stock, smooth
it over several time scales (weekly / monthly / quarterly / yearly averages), and
test whether the index RISING or FALLING relates to the stock's return.

Two stock-level search series are tested:
- ``composite``  : z-score each of the stock's keyword series (datalab_patent_keywords.csv)
                   over its full history, then average them per week → a scale-free
                   "average standardized search interest" across many keywords.
- ``stockname``  : the stock's NAME query (삼성전자 / SK하이닉스 / 네이버), the purest
                   single attention proxy (stockname_datalab.csv).

For each horizon H (weeks), with ``avg_H[t]`` = mean of the search index over the H
weeks ending at t:
  search_mom_H[t]   = avg_H[t] - avg_H[t-H]                 (change between adjacent H-windows)
  fwd_excess_H[t]   = stock H-week return - benchmark's, t -> t+H   (FUTURE)
  coin_excess_H[t]  = same but t-H -> t                            (CONCURRENT)

We report, pooled over the 3 stocks per horizon:
  corr_predict  = corr(search_mom_H, fwd_excess_H)   -- does a search up/down-trend PREDICT?
  corr_coincide = corr(search_mom_H, coin_excess_H)  -- does it move WITH the price?
  rise-minus-fall = mean fwd return when search rose (top tercile) minus when it fell (bottom)

CRITICAL: we correlate CHANGES (momentum), never levels — both search and price
trended up over 2016-2026, so a level correlation would be spurious. We also print
n_indep = n/H: long horizons have few independent windows, so a big corr there is
likely noise (the small-sample mirage that already burned us once).
"""
from __future__ import annotations

import argparse
import statistics
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.period_keyword_dataset import load_keyword_series  # noqa: E402
from app.ml.prices_csv import load_prices_csv  # noqa: E402
from scripts.event_study_leadlag import pearson, week_index, zscore  # noqa: E402

HORIZONS = {1: "1주", 4: "1개월", 13: "1분기", 52: "1년"}


def weekly_closes_by_week(ps) -> dict[int, float]:
    """{week_index: last close of that ISO week}."""
    out: dict[int, float] = {}
    last_day: dict[int, date] = {}
    for d, c in zip(ps.dates, ps.closes):
        w = week_index(d)
        if w not in last_day or d > last_day[w]:
            last_day[w] = d
            out[w] = c
    return out


def composite_index(series_by_kw, ticker) -> dict[int, float]:
    """Mean of the ticker's z-scored keyword series, per ISO week."""
    per_week_vals: dict[int, list[float]] = {}
    for (t, _kw), points in series_by_kw.items():
        if t != ticker or len(points) < 5:
            continue
        vals = [v for _, v in points]
        zs = zscore(vals)
        if zs is None:
            continue
        for (d, _), z in zip(points, zs):
            if z is not None:
                per_week_vals.setdefault(week_index(d), []).append(z)
    return {w: statistics.mean(v) for w, v in per_week_vals.items() if v}


def name_index(series_by_kw, ticker) -> dict[int, float]:
    """The single stock-name series as {week_index: ratio} (last wins)."""
    out: dict[int, float] = {}
    for (t, _kw), points in series_by_kw.items():
        if t != ticker:
            continue
        for d, v in points:
            out[week_index(d)] = v
    return out


def _avg_over(series: dict[int, float], end_week: int, h: int) -> float | None:
    """Mean of the search index over the H weeks ending at ``end_week`` (>= half present)."""
    vals = [series[w] for w in range(end_week - h + 1, end_week + 1) if w in series]
    if len(vals) < max(1, h // 2):
        return None
    return statistics.mean(vals)


def _hret(closes: dict[int, float], a: int, b: int) -> float | None:
    """Percent return from week a to week b."""
    ca, cb = closes.get(a), closes.get(b)
    if ca is None or cb is None or ca == 0:
        return None
    return (cb / ca - 1.0) * 100.0


def analyze(label, index_fn, series_by_kw, closes_by_ticker, bench_closes, tickers):
    print(f"\n{'='*72}\n[{label}]  검색 증감 → 수익 (changes, not levels; 3종목 pooled)\n{'='*72}")
    print(f"{'horizon':>8} {'n':>5} {'n_indep':>7} {'corr_predict':>12} {'corr_coincide':>13} "
          f"{'상승-하락_fwd%':>14}")
    for h in HORIZONS:
        mom, fwd, coin = [], [], []
        for t in tickers:
            search = index_fn(series_by_kw, t)
            closes = closes_by_ticker.get(t)
            if not search or not closes:
                continue
            weeks = sorted(set(search) & set(closes))
            for w in weeks:
                a_now = _avg_over(search, w, h)
                a_prev = _avg_over(search, w - h, h)
                if a_now is None or a_prev is None:
                    continue
                f = _hret(closes, w, w + h)
                fb = _hret(bench_closes, w, w + h)
                c = _hret(closes, w - h, w)
                cb = _hret(bench_closes, w - h, w)
                # Drop the obs if any leg is missing — a None benchmark must NOT be
                # treated as a 0% benchmark return (that would overstate excess).
                if None in (f, fb, c, cb):
                    continue
                mom.append(a_now - a_prev)
                fwd.append(f - fb)
                coin.append(c - cb)
        cp = pearson(mom, fwd)
        cc = pearson(mom, coin)
        # rise (top tercile of momentum) minus fall (bottom tercile): mean forward return
        spread = _tercile_spread(mom, fwd)
        n_indep = len(mom) // h if h else len(mom)
        print(f"{HORIZONS[h]:>8} {len(mom):>5} {n_indep:>7} "
              f"{_f(cp):>12} {_f(cc):>13} {_f(spread):>14}")
    print("  Read: corr_predict>0 (큰 n_indep)면 검색 증감이 미래수익 선행. corr_coincide만 크고 "
          "predict≈0이면 동시(영향X). 장기 horizon은 n_indep 작아 노이즈 주의.")


def _tercile_spread(mom, fwd):
    pairs = sorted(zip(mom, fwd), key=lambda p: p[0])
    n = len(pairs)
    if n < 9:
        return None
    k = n // 3
    bottom = [f for _, f in pairs[:k]]
    top = [f for _, f in pairs[-k:]]
    return statistics.mean(top) - statistics.mean(bottom)


def _f(v):
    return f"{v:>+.3f}" if isinstance(v, float) else f"{'n/a':>6}"


def main() -> int:
    p = argparse.ArgumentParser(description="Aggregate stock-level search momentum vs returns")
    p.add_argument("--keyword-csv", default="datalab_patent_keywords.csv")
    p.add_argument("--stockname-csv", default="stockname_datalab.csv")
    p.add_argument("--prices-csv", required=True)
    p.add_argument("--benchmark", default="KS11")
    p.add_argument("--tickers", default="005930,000660,035420")
    args = p.parse_args()

    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    prices = load_prices_csv(args.prices_csv)
    bench_closes = weekly_closes_by_week(prices[args.benchmark]) if args.benchmark in prices else {}
    closes_by_ticker = {t: weekly_closes_by_week(prices[t]) for t in tickers if t in prices}

    composite_series = load_keyword_series(args.keyword_csv)
    name_series = load_keyword_series(args.stockname_csv)

    analyze("composite (키워드 풀 평균)", composite_index, composite_series,
            closes_by_ticker, bench_closes, tickers)
    analyze("stockname (종목명 검색)", name_index, name_series,
            closes_by_ticker, bench_closes, tickers)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
