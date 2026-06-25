#!/usr/bin/env python
"""Pin each weekly event to its exact biggest-move DAY (Stage 7).

Stage 6 selected events at WEEKLY resolution. To study the 1-day DataLab
publication lag we need the precise trading day the price moved. For each event,
within its ISO week we take the single trading day with the largest |excess daily
return| (stock daily return - benchmark's) and record it as ``move_day``.

Reuses the daily price CSV (``prices_csv.load_prices_csv``) — no DB, no API.

Usage:
    uv run python scripts/detect_event_move_day.py \
        --events events_with_keywords.json --prices-csv prices_kospi15_2016_2026.csv \
        --benchmark KS11 --out events_with_moveday.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.prices_csv import load_prices_csv  # noqa: E402


def daily_excess_returns(ps, bench) -> dict[date, float]:
    """{trade_date: stock_daily_return_pct - benchmark_daily_return_pct}."""
    def rets(series_dates, series_closes):
        out = {}
        for i in range(1, len(series_closes)):
            prev = series_closes[i - 1]
            if prev:
                out[series_dates[i]] = (series_closes[i] / prev - 1.0) * 100.0
        return out

    stock = rets(ps.dates, ps.closes)
    bench_r = rets(bench.dates, bench.closes) if bench is not None else {}
    return {d: r - bench_r.get(d, 0.0) for d, r in stock.items()}


def main() -> int:
    p = argparse.ArgumentParser(description="Find each event's exact biggest-move day")
    p.add_argument("--events", required=True)
    p.add_argument("--prices-csv", required=True)
    p.add_argument("--benchmark", default="KS11")
    p.add_argument("--out", default="events_with_moveday.json")
    args = p.parse_args()

    prices = load_prices_csv(args.prices_csv)
    bench = prices.get(args.benchmark)
    excess_by_ticker = {
        t: daily_excess_returns(ps, bench) for t, ps in prices.items() if t != args.benchmark
    }

    events = json.load(open(args.events, encoding="utf-8"))
    out = []
    for ev in events:
        if not ev.get("keyword"):
            continue
        wk = date.fromisoformat(ev["week_date"]).isocalendar()[:2]
        ex = excess_by_ticker.get(ev["ticker"], {})
        # trading days in the event's ISO week, pick max |excess|
        in_week = [(d, r) for d, r in ex.items() if d.isocalendar()[:2] == wk]
        if not in_week:
            print(f"  ! {ev['ticker']} {ev['week_date']}: no daily prices in week, skip")
            continue
        move_day, move_ret = max(in_week, key=lambda kv: abs(kv[1]))
        e = dict(ev)
        e["move_day"] = move_day.isoformat()
        e["move_day_excess_pct"] = round(move_ret, 2)
        out.append(e)

    json.dump(out, open(args.out, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(f"[out] {len(out)} events with move_day -> {args.out}")
    for e in out:
        print(f"  {e['ticker']}  week {e['week_date']}  move_day {e['move_day']}  "
              f"{e['move_day_excess_pct']:>+6.2f}%  {e['keyword']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
