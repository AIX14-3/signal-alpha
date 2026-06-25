#!/usr/bin/env python
"""Detect the biggest stock-specific price-move weeks per ticker (Stage 6).

Objective, hindsight-free EVENT DETECTION: from the local price CSV, resample to
weekly closes, compute each week's return MINUS the benchmark's (excess return,
so a market-wide move is not mistaken for a stock event), and keep the top-N
weeks by ``|excess|`` per ticker — de-duplicated so two adjacent weeks of the
same shock count once.

The weeks it returns are the anchors for the lead-lag event study: later steps
name the driving event for each and pull that keyword's DataLab search series.
This script touches NO keyword/DataLab data and uses price only, so the event
SET is selected without any search-side information (only the event NAMING, done
later, uses hindsight — and that is fine for a validity / lead-lag study).

Usage:
    uv run python scripts/detect_price_events.py \
        --prices-csv prices_kospi15_2016_2026.csv --benchmark KS11 \
        --tickers 005930,000660,035420 --top-n 10 --gap-weeks 4 \
        --out price_events.json
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.ml.prices_csv import load_prices_csv  # noqa: E402


def weekly_last_close(dates: list[date], closes: list[float]) -> list[tuple[date, float]]:
    """Collapse daily closes to one (last trading day, close) per ISO (year, week)."""
    by_week: dict[tuple[int, int], tuple[date, float]] = {}
    for d, c in zip(dates, closes):
        key = d.isocalendar()[:2]  # (iso_year, iso_week)
        prev = by_week.get(key)
        if prev is None or d > prev[0]:
            by_week[key] = (d, c)
    return [by_week[k] for k in sorted(by_week)]


def weekly_returns(weekly: list[tuple[date, float]]) -> dict[date, float]:
    """Week-over-week percent return keyed by the week's last trading date."""
    out: dict[date, float] = {}
    for (_, prev_c), (d, c) in zip(weekly, weekly[1:]):
        if prev_c:
            out[d] = (c / prev_c - 1.0) * 100.0
    return out


def detect_events(
    *,
    prices_by_ticker,
    tickers: list[str],
    benchmark_ticker: str,
    top_n: int,
    gap_weeks: int,
) -> list[dict]:
    bench = prices_by_ticker.get(benchmark_ticker)
    bench_ret = (
        weekly_returns(weekly_last_close(bench.dates, bench.closes)) if bench else {}
    )
    events: list[dict] = []
    for ticker in tickers:
        ps = prices_by_ticker.get(ticker)
        if ps is None:
            continue
        weekly = weekly_last_close(ps.dates, ps.closes)
        rets = weekly_returns(weekly)
        # Excess return per week (fall back to raw when benchmark week missing).
        excess = {d: r - bench_ret.get(d, 0.0) for d, r in rets.items()}
        ranked = sorted(excess.items(), key=lambda kv: abs(kv[1]), reverse=True)
        picked: list[date] = []
        for d, ex in ranked:
            if any(abs((d - p).days) < gap_weeks * 7 for p in picked):
                continue  # same shock as an already-picked nearby week
            picked.append(d)
            events.append(
                {
                    "ticker": ticker,
                    "week_date": d.isoformat(),
                    "excess_return_pct": round(ex, 2),
                    "direction": "up" if ex > 0 else "down",
                }
            )
            if len(picked) >= top_n:
                break
    events.sort(key=lambda e: (e["ticker"], e["week_date"]))
    return events


def main() -> int:
    p = argparse.ArgumentParser(description="Detect biggest excess-return weeks per ticker")
    p.add_argument("--prices-csv", required=True)
    p.add_argument("--benchmark", default="KS11")
    p.add_argument("--tickers", required=True, help="comma-separated tickers")
    p.add_argument("--top-n", type=int, default=10)
    p.add_argument("--gap-weeks", type=int, default=4)
    p.add_argument("--out", default="price_events.json")
    args = p.parse_args()

    prices_by_ticker = load_prices_csv(args.prices_csv)
    tickers = [t.strip() for t in args.tickers.split(",") if t.strip()]
    events = detect_events(
        prices_by_ticker=prices_by_ticker,
        tickers=tickers,
        benchmark_ticker=args.benchmark,
        top_n=args.top_n,
        gap_weeks=args.gap_weeks,
    )
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(events, fh, ensure_ascii=False, indent=2)
    print(f"[out] {len(events)} events -> {args.out}")
    for e in events:
        print(f"  {e['ticker']}  {e['week_date']}  {e['excess_return_pct']:>+7.2f}%  {e['direction']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
