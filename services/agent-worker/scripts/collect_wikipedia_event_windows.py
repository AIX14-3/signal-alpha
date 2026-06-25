#!/usr/bin/env python
"""Collect en.Wikipedia DAILY pageviews in a window around each event move day.

A free, keyless, ABSOLUTE-count attention proxy to compare against Naver DataLab:
does an alternative attention source LEAD the price move where DataLab only
coincides? Output is written in the exact schema ``event_study_daily.py`` already
consumes, so the same lead-lag harness runs unchanged.

Wikimedia REST pageviews API (no key; descriptive User-Agent required):
  /metrics/pageviews/per-article/en.wikipedia/all-access/user/{article}/daily/{start}/{end}
Returns absolute daily ``views`` (bot-filtered via agent=user). Data exists from
2015-07-01; we only serve up to yesterday.

Output CSV ``wiki_event_daily.csv``: event_id, ticker, keyword(article), period, ratio(views).
Resume via a ``.done`` sidecar (event_id|article).

Usage:
    uv run python scripts/collect_wikipedia_event_windows.py \
        --events events_with_moveday.json --out wiki_event_daily.csv --window-days 15
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.parse
import urllib.request
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# Canonical en.wikipedia article titles for each ticker (pageviews API does NOT
# follow redirects, so titles must be exact).
ARTICLE_BY_TICKER = {
    "005930": "Samsung_Electronics",
    "000660": "SK_Hynix",
    "035420": "Naver_Corporation",
}

UA = "signal-alpha-research/1.0 (altdata lead-lag study; contact: research@example.com)"
API = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
       "en.wikipedia/all-access/user/{article}/daily/{start}/{end}")


def fetch_daily_views(article: str, start: date, end: date) -> list[tuple[date, float]]:
    url = API.format(
        article=urllib.parse.quote(article, safe=""),
        start=start.strftime("%Y%m%d"),
        end=end.strftime("%Y%m%d"),
    )
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    out = []
    for item in data.get("items", []):
        ts = item.get("timestamp", "")
        try:
            d = date(int(ts[0:4]), int(ts[4:6]), int(ts[6:8]))
        except (ValueError, IndexError):
            continue
        out.append((d, float(item.get("views", 0))))
    return out


def _mark_done(done_fh, done: set[str], tag: str) -> None:
    done_fh.write(tag + "\n")
    done_fh.flush()
    done.add(tag)


def main() -> int:
    p = argparse.ArgumentParser(description="Collect en.Wikipedia daily pageviews around event move days")
    p.add_argument("--events", default="events_with_moveday.json")
    p.add_argument("--out", default="wiki_event_daily.csv")
    p.add_argument("--window-days", type=int, default=15)
    args = p.parse_args()

    with open(args.events, encoding="utf-8") as fh:
        events = [e for e in json.load(fh) if e.get("keyword")]
    yesterday = date.today() - timedelta(days=1)

    out_path = Path(args.out)
    done_path = out_path.with_suffix(out_path.suffix + ".done")
    done = set(done_path.read_text(encoding="utf-8").splitlines()) if done_path.exists() else set()
    csv_exists = out_path.exists()

    csv_fh = open(out_path, "a", newline="", encoding="utf-8")
    writer = csv.writer(csv_fh)
    if not csv_exists:
        writer.writerow(["event_id", "ticker", "keyword", "period", "ratio"])
    done_fh = open(done_path, "a", encoding="utf-8")

    ok = failed = 0
    try:
        for ev in events:
            ticker = ev["ticker"]
            article = ARTICLE_BY_TICKER.get(ticker)
            if not article:
                print(f"  ! {ticker}: no article mapping, skip")
                continue
            event_id = f"{ticker}|{ev['week_date']}"
            tag = f"{event_id}|{article}"
            if tag in done:
                continue
            move_day = date.fromisoformat(ev["move_day"])
            start = move_day - timedelta(days=args.window_days)
            end = min(move_day + timedelta(days=args.window_days), yesterday)
            try:
                rows = fetch_daily_views(article, start, end)
            except Exception as exc:  # network/HTTP -> retry next run
                print(f"  ! {tag}: {exc}")
                failed += 1
                continue
            writer.writerows((event_id, ticker, article, d.isoformat(), v) for d, v in rows)
            nz = sum(1 for _, v in rows if v > 0)
            print(f"{event_id}  {article}: {len(rows)} days, {nz} nonzero  [{start}..{end}]")
            ok += 1
            _mark_done(done_fh, done, tag)
    finally:
        csv_fh.close()
        done_fh.close()

    print(f"\n[out] appended to {args.out}  (resume sidecar: {done_path.name})")
    print(f"this run: {ok} events collected, {failed} failed (re-run to retry)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
