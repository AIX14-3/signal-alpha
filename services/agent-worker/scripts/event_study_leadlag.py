#!/usr/bin/env python
"""Lead-lag event study: does event-keyword DataLab search LEAD, COINCIDE, or LAG
the big price move it belongs to? (Stage 6)

Inputs:
- events JSON  : [{ticker, week_date, keyword, ...}]  (price-detected big moves,
                 each named + given a Naver-searchable keyword)
- keyword CSV  : ticker,keyword,period,ratio  (weekly DataLab search for those keywords)
- prices CSV   : ticker,date,close            (+ a benchmark ticker)

Two complementary readouts, both centred on the question "could DataLab have seen
it coming, given a perfect keyword?":

(A) EVENT-CENTRED PROFILE — for every event, z-score the keyword's search index in
    a [-K..+K] week window around the move week, then average by relative week.
    Where the averaged search peak sits says it all:
        peak at a NEGATIVE offset  -> search LEADS  (keyword choice was the bottleneck)
        peak at  0                 -> COINCIDENT    (no tradeable lead)
        peak at a POSITIVE offset  -> search LAGS   (DataLab is reactive)

(B) FULL-SERIES CROSS-CORRELATION — for each (ticker, keyword) over the WHOLE
    2016-2026 history (not just event weeks, so this part is not conditioned on the
    outcome), correlate weekly search momentum with weekly excess return at lags
    -K..+K. corr at lag>0 = search leads the return; lag<0 = search lags it.

Hindsight note: the EVENTS are price-selected and the KEYWORDS are named with
hindsight, so (A) is a validity/timing study, NOT a predictive backtest. (B)'s
lead side is the closest thing to "was there latent predictive content".

Usage:
    uv run python scripts/event_study_leadlag.py \
        --events events_with_keywords.json --keyword-csv event_datalab.csv \
        --prices-csv prices_kospi15_2016_2026.csv --benchmark KS11 --k 8
"""
from __future__ import annotations

import argparse
import json
import sys
import statistics
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.prices_csv import load_prices_csv  # noqa: E402
from app.ml.period_keyword_dataset import load_keyword_series  # noqa: E402


# --------------------------------------------------------------------------- #
# Weekly price helpers (resample daily closes -> weekly excess returns)
# --------------------------------------------------------------------------- #
def weekly_last_close(dates: list[date], closes: list[float]) -> list[tuple[date, float]]:
    by_week: dict[tuple[int, int], tuple[date, float]] = {}
    for d, c in zip(dates, closes):
        key = d.isocalendar()[:2]
        prev = by_week.get(key)
        if prev is None or d > prev[0]:
            by_week[key] = (d, c)
    return [by_week[k] for k in sorted(by_week)]


def weekly_excess_returns(
    ps, bench
) -> list[tuple[date, float]]:
    """[(week_last_day, excess_return_pct)] = stock weekly return - benchmark's."""
    stock_w = weekly_last_close(ps.dates, ps.closes)
    bench_ret = {}
    if bench is not None:
        bw = weekly_last_close(bench.dates, bench.closes)
        for (_, pc), (d, c) in zip(bw, bw[1:]):
            if pc:
                bench_ret[d.isocalendar()[:2]] = (c / pc - 1.0) * 100.0
    out = []
    for (_, pc), (d, c) in zip(stock_w, stock_w[1:]):
        if pc:
            r = (c / pc - 1.0) * 100.0
            out.append((d, r - bench_ret.get(d.isocalendar()[:2], 0.0)))
    return out


# --------------------------------------------------------------------------- #
# ISO-week alignment
# --------------------------------------------------------------------------- #
def week_index(d: date) -> int:
    """Monotonic integer index of d's ISO week (consecutive weeks differ by 1).

    Both series live on different weekdays — Naver weekly points fall on MONDAY,
    price weeks on the FRIDAY (last trading day). Keying everything by ISO-week
    index removes the Mon/Fri gap so offset 0 is unambiguously the SAME week (a
    naive nearest-date match with a tolerance would snap to the adjacent week and
    silently shift every offset by one).
    """
    monday = d - timedelta(days=d.weekday())
    return (monday.toordinal() - 1) // 7


def series_by_week(series: list[tuple[date, float]]) -> dict[int, float]:
    """Map a weekly (date, value) series to {week_index: value} (last wins on dupes)."""
    return {week_index(d): v for d, v in series}


def zscore(values: list[float]) -> list[float] | None:
    vals = [v for v in values if v is not None]
    if len(vals) < 3:
        return None
    mu = statistics.mean(vals)
    sd = statistics.pstdev(vals)
    if sd == 0:
        return None
    return [None if v is None else (v - mu) / sd for v in values]


def pearson(xs: list[float], ys: list[float]) -> float | None:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    if len(pairs) < 8:
        return None
    xv = [p[0] for p in pairs]
    yv = [p[1] for p in pairs]
    mx, my = statistics.mean(xv), statistics.mean(yv)
    num = sum((x - mx) * (y - my) for x, y in pairs)
    dx = sum((x - mx) ** 2 for x in xv) ** 0.5
    dy = sum((y - my) ** 2 for y in yv) ** 0.5
    if dx == 0 or dy == 0:
        return None
    return num / (dx * dy)


# --------------------------------------------------------------------------- #
# (A) Event-centred search profile
# --------------------------------------------------------------------------- #
def event_profile(events, series_by_kw, k: int):
    offsets = list(range(-k, k + 1))
    cols: dict[int, list[float]] = {o: [] for o in offsets}
    used = 0
    for ev in events:
        key = (ev["ticker"], ev["keyword"])
        series = series_by_kw.get(key)
        if not series:
            continue
        wk_ratio = series_by_week(series)
        ew = week_index(date.fromisoformat(ev["week_date"]))  # offset 0 == move week
        window = [wk_ratio.get(ew + o) for o in offsets]
        zs = zscore(window)
        if zs is None:
            continue
        used += 1
        for o, z in zip(offsets, zs):
            if z is not None:
                cols[o].append(z)
    profile = {
        o: (statistics.mean(cols[o]) if cols[o] else None, len(cols[o])) for o in offsets
    }
    return profile, used


# --------------------------------------------------------------------------- #
# (B) Full-series cross-correlation (search momentum vs excess return)
# --------------------------------------------------------------------------- #
def crosscorr(events, series_by_kw, excess_by_ticker, k: int):
    lags = list(range(-k, k + 1))
    per_lag: dict[int, list[float]] = {lag: [] for lag in lags}
    pairs_used = 0
    seen = set()
    for ev in events:
        key = (ev["ticker"], ev["keyword"])
        if key in seen:
            continue
        seen.add(key)
        series = series_by_kw.get(key)
        excess = excess_by_ticker.get(ev["ticker"])
        if not series or not excess:
            continue
        swk = series_by_week(series)        # week_index -> search ratio
        ewk = series_by_week(excess)        # week_index -> excess return
        # True 1-week search momentum (only when the immediately prior week exists).
        mom: dict[int, float] = {}
        for w, v in swk.items():
            prev = swk.get(w - 1)
            if prev:
                mom[w] = v / prev - 1.0
        contributed = False
        for lag in lags:
            xs, ys = [], []
            for w, m in mom.items():
                r = ewk.get(w + lag)  # lag>0: return is LATER -> search leads
                if r is not None:
                    xs.append(m)
                    ys.append(r)
            c = pearson(xs, ys)
            if c is not None:
                per_lag[lag].append(c)
                contributed = True
        if contributed:
            pairs_used += 1
    summary = {
        lag: (statistics.mean(per_lag[lag]) if per_lag[lag] else None, len(per_lag[lag]))
        for lag in lags
    }
    return summary, pairs_used


def main() -> int:
    p = argparse.ArgumentParser(description="Lead-lag event study for event-keyword DataLab")
    p.add_argument("--events", required=True)
    p.add_argument("--keyword-csv", required=True)
    p.add_argument("--prices-csv", required=True)
    p.add_argument("--benchmark", default="KS11")
    p.add_argument("--k", type=int, default=8, help="window half-width in weeks")
    args = p.parse_args()

    events = json.load(open(args.events, encoding="utf-8"))
    events = [e for e in events if e.get("keyword")]
    series_by_kw = load_keyword_series(args.keyword_csv)
    prices = load_prices_csv(args.prices_csv)
    bench = prices.get(args.benchmark)
    excess_by_ticker = {
        t: weekly_excess_returns(ps, bench)
        for t, ps in prices.items()
        if t != args.benchmark
    }

    prof, used_a = event_profile(events, series_by_kw, args.k)
    print(f"\n(A) EVENT-CENTRED SEARCH PROFILE  (z-scored, {used_a}/{len(events)} events usable)")
    print(f"{'offset_wk':>9} {'mean_z':>8} {'n':>4}   (neg=before move, 0=move week)")
    best = None
    for o in range(-args.k, args.k + 1):
        m, n = prof[o]
        bar = ""
        if m is not None:
            bar = "#" * int(round(abs(m) * 20))
            if best is None or m > best[1]:
                best = (o, m)
        ms = f"{m:>+8.2f}" if m is not None else f"{'n/a':>8}"
        marker = " <-- move" if o == 0 else ""
        print(f"{o:>9} {ms} {n:>4}  {bar}{marker}")
    if best:
        where = "LEADS (before move)" if best[0] < 0 else (
            "COINCIDENT" if best[0] == 0 else "LAGS (after move)")
        print(f"  => search peak at offset {best[0]:+d} wk  =>  {where}")

    cc, used_b = crosscorr(events, series_by_kw, excess_by_ticker, args.k)
    print(f"\n(B) FULL-SERIES CROSS-CORR  search momentum vs excess return  "
          f"({used_b} keyword-stock pairs)")
    print(f"{'lag_wk':>7} {'corr':>8} {'n':>4}   (lag>0: search LEADS return; lag<0: search LAGS)")
    best_c = None
    for lag in range(-args.k, args.k + 1):
        m, n = cc[lag]
        ms = f"{m:>+8.3f}" if m is not None else f"{'n/a':>8}"
        if m is not None and (best_c is None or abs(m) > abs(best_c[1])):
            best_c = (lag, m)
        print(f"{lag:>7} {ms} {n:>4}")
    if best_c:
        print(f"  => strongest |corr| at lag {best_c[0]:+d} wk = {best_c[1]:+.3f}")
    print("\nRead: a real predictive lead needs corr clearly >0 at lag>0 AND a search "
          "peak at negative offset in (A). Coincident/lag = DataLab is reactive.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
