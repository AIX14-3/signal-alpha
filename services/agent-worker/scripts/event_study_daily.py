#!/usr/bin/env python
"""Daily lead-lag + PUBLICATION-LAG study (Stage 7).

Stage 6 found DataLab search is COINCIDENT with the price move at weekly
resolution. But DataLab's daily index is "previous-day confirmed": day D's value
is only retrievable on D+1. So even a coincident signal may be useless — by the
time you receive it, the price already moved. This script tests that at DAILY
resolution and quantifies the cost of the 1-day availability lag.

Inputs:
- events JSON  : events_with_moveday.json  [{event_id?, ticker, week_date, keyword, move_day, ...}]
- daily search : event_daily_datalab.csv   (event_id, ticker, keyword, period, ratio)  [daily]
- prices CSV   : ticker,date,close (daily) + a benchmark ticker

(A) DAILY SEARCH PROFILE around the move day (offset 0 == move_day, CALENDAR days):
    z-score each event's search in [-K..+K] days, average by offset. We read off:
      day  0 ("당일")  = the coincident peak — but only RECEIVED on D+1
      day -1 ("전일")  = the freshest value you actually HOLD when acting on day 0
      day +1 ("하루 뒤") = the next day
    Headline: Delta_lag = z(0) - z(-1) = the spike you cannot see in time.

(B) DAILY CROSS-CORR (publication-lag cost): corr(search_momentum[d], excess_return[d+lag]).
      lag  0 = coincident (hypothetical real-time search) — the optimistic ceiling
      lag +1 = search leads return by a day = the ONLY realistically tradeable cell
               (D's search arrives D+1 morning, so you could act on D+1).
    If lag 0 is non-trivial but lag +1 ~ 0, the publication lag erases the signal.

Reuses zscore()/pearson() from event_study_leadlag.py. Day-indexing is by trading
day for prices and by calendar day for search, joined on the actual date (search
is published per calendar day; we anchor everything on move_day).

Usage:
    uv run python scripts/event_study_daily.py --events events_with_moveday.json \
        --daily-csv event_daily_datalab.csv --prices-csv prices_kospi15_2016_2026.csv \
        --benchmark KS11 --k 10
"""
from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.prices_csv import load_prices_csv  # noqa: E402
from scripts.event_study_leadlag import pearson, zscore  # noqa: E402


def load_daily_by_event(csv_path: str) -> dict[str, dict[date, float]]:
    """{event_id: {calendar_date: search_ratio}} from the daily windows CSV."""
    out: dict[str, dict[date, float]] = {}
    with open(csv_path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            try:
                d = date.fromisoformat(row["period"][:10])
                r = float(row["ratio"])
            except (ValueError, TypeError, KeyError):
                continue
            out.setdefault(row["event_id"], {})[d] = r
    return out


def daily_excess_returns(ps, bench) -> dict[date, float]:
    """{trade_date: stock daily return - benchmark daily return} in percent."""
    def rets(dates, closes):
        return {
            dates[i]: (closes[i] / closes[i - 1] - 1.0) * 100.0
            for i in range(1, len(closes))
            if closes[i - 1]
        }

    stock = rets(ps.dates, ps.closes)
    bench_r = rets(bench.dates, bench.closes) if bench is not None else {}
    return {d: r - bench_r.get(d, 0.0) for d, r in stock.items()}


# --------------------------------------------------------------------------- #
# (A) Daily search profile around move_day (calendar-day offsets)
# --------------------------------------------------------------------------- #
def search_profile(events, daily_by_event, k: int):
    offsets = list(range(-k, k + 1))
    cols: dict[int, list[float]] = {o: [] for o in offsets}
    used = 0
    for ev in events:
        eid = f"{ev['ticker']}|{ev['week_date']}"
        series = daily_by_event.get(eid)
        if not series:
            continue
        anchor = date.fromisoformat(ev["move_day"])
        window = [series.get(anchor + timedelta(days=o)) for o in offsets]
        zs = zscore(window)
        if zs is None:
            continue
        used += 1
        for o, z in zip(offsets, zs):
            if z is not None:
                cols[o].append(z)
    profile = {o: (statistics.mean(cols[o]) if cols[o] else None, len(cols[o])) for o in offsets}
    return profile, used


# --------------------------------------------------------------------------- #
# (B) Daily cross-correlation: search momentum vs excess return, by lag
# --------------------------------------------------------------------------- #
def daily_crosscorr(events, daily_by_event, excess_by_ticker, k: int):
    lags = list(range(-k, k + 1))
    per_lag: dict[int, list[float]] = {lag: [] for lag in lags}
    used = 0
    for ev in events:
        eid = f"{ev['ticker']}|{ev['week_date']}"
        series = daily_by_event.get(eid)
        excess = excess_by_ticker.get(ev["ticker"])
        if not series or not excess:
            continue
        days = sorted(series)
        # 1-day search momentum on consecutive CALENDAR days present in the window.
        mom: dict[date, float] = {}
        for d in days:
            prev = series.get(d - timedelta(days=1))
            if prev:
                mom[d] = series[d] / prev - 1.0
        contributed = False
        for lag in lags:
            xs, ys = [], []
            for d, m in mom.items():
                r = excess.get(d + timedelta(days=lag))  # lag>0: return LATER -> search leads
                if r is not None:
                    xs.append(m)
                    ys.append(r)
            c = pearson(xs, ys)
            if c is not None:
                per_lag[lag].append(c)
                contributed = True
        if contributed:
            used += 1
    summary = {lag: (statistics.mean(per_lag[lag]) if per_lag[lag] else None, len(per_lag[lag]))
               for lag in lags}
    return summary, used


def _fmt(v):
    return f"{v:>+8.2f}" if v is not None else f"{'n/a':>8}"


def main() -> int:
    p = argparse.ArgumentParser(description="Daily publication-lag event study")
    p.add_argument("--events", required=True)
    p.add_argument("--daily-csv", required=True)
    p.add_argument("--prices-csv", required=True)
    p.add_argument("--benchmark", default="KS11")
    p.add_argument("--k", type=int, default=10, help="window half-width in days")
    args = p.parse_args()

    events = [e for e in json.load(open(args.events, encoding="utf-8")) if e.get("keyword")]
    daily_by_event = load_daily_by_event(args.daily_csv)
    prices = load_prices_csv(args.prices_csv)
    bench = prices.get(args.benchmark)
    excess_by_ticker = {
        t: daily_excess_returns(ps, bench) for t, ps in prices.items() if t != args.benchmark
    }

    prof, used_a = search_profile(events, daily_by_event, args.k)
    print(f"\n(A) DAILY SEARCH PROFILE around move_day  (z-scored, {used_a}/{len(events)} events)")
    print(f"{'offset_d':>8} {'mean_z':>8} {'n':>4}   (0 = move day; -1 = held when acting on day 0)")
    peak = None
    for o in range(-args.k, args.k + 1):
        m, n = prof[o]
        if m is not None and (peak is None or m > peak[1]):
            peak = (o, m)
        bar = "#" * int(round(abs(m) * 20)) if m is not None else ""
        mark = {0: " <-- 당일(move)", -1: " <-- 전일(held)", 1: " <-- 하루뒤"}.get(o, "")
        print(f"{o:>8} {_fmt(m)} {n:>4}  {bar}{mark}")
    z0 = prof[0][0]
    zm1 = prof[-1][0]
    zp1 = prof[1][0]
    if peak:
        where = "LEADS" if peak[0] < 0 else ("COINCIDENT" if peak[0] == 0 else "LAGS")
        print(f"  => search peak at day {peak[0]:+d}  =>  {where}")
    if z0 is not None and zm1 is not None:
        print(f"  => Delta_lag = z(0) - z(-1) = {z0 - zm1:+.2f}  "
              f"(당일 {z0:+.2f} vs 전일/보유 {zm1:+.2f}; 하루뒤 {_fmt(zp1).strip()})")
        print("     = 발행지연으로 변동 당일에 '제때 못 보는' 검색 정보량.")

    cc, used_b = daily_crosscorr(events, daily_by_event, excess_by_ticker, args.k)
    print(f"\n(B) DAILY CROSS-CORR  search momentum vs excess return  ({used_b} events)")
    print(f"{'lag_d':>6} {'corr':>8} {'n':>4}   (lag>0: search LEADS; lag+1 = realistic tradeable cell)")
    for lag in range(-args.k, args.k + 1):
        m, n = cc[lag]
        mark = {0: " <-- 동시(실시간 가정)", 1: " <-- lag+1 (발행지연 후 현실)"}.get(lag, "")
        print(f"{lag:>6} {_fmt2(m)} {n:>4}{mark}")
    c0 = cc[0][0]
    c1 = cc[1][0]
    print(f"\n  => 동시 lag0 = {_fmt2(c0).strip()}  vs  현실 lag+1 = {_fmt2(c1).strip()}")
    print("  Read: lag0가 의미있는데 lag+1≈0이면 발행지연이 동시 신호마저 제거(= 받는 데이터는 사후값).")
    return 0


def _fmt2(v):
    return f"{v:>+8.3f}" if v is not None else f"{'n/a':>8}"


if __name__ == "__main__":
    raise SystemExit(main())
