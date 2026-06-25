#!/usr/bin/env python
"""Collect DAILY DataLab search in a window around each event's move day (Stage 7).

The full-range collector (``collect_datalab_for_keywords.py``) pulls one weekly
series per keyword over all years — wrong granularity here. For the publication-lag
study we need DAILY search in a tight window around each event's exact move day.

For every event in ``events_with_moveday.json`` we request the keyword's daily
search index over ``[move_day - W, min(move_day + W, yesterday)]`` (Naver only
serves up to yesterday — the very lag we are studying) and write rows tagged by
event so the same keyword used by two events stays separate.

Resume-safe: a ``.done`` sidecar records every (event_id|keyword) with a definitive
result; quota/network failures are not recorded so a re-run retries only them.
Local CSV only — no prod writes.

Usage:
    uv run python scripts/collect_datalab_daily_windows.py \
        --events events_with_moveday.json --out event_daily_datalab.csv --window-days 15
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _mark_done(done_fh, done: set[str], tag: str) -> None:
    done_fh.write(tag + "\n")
    done_fh.flush()
    done.add(tag)


async def _run(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Collect daily DataLab windows around event move days")
    p.add_argument("--events", default="events_with_moveday.json")
    p.add_argument("--out", default="event_daily_datalab.csv")
    p.add_argument("--window-days", type=int, default=15, help="+/- calendar days around move_day")
    args = p.parse_args(argv)

    try:  # let a repo-root .env supply NAVER creds without manual export
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    cid = os.environ.get("NAVER_CLIENT_ID")
    csec = os.environ.get("NAVER_CLIENT_SECRET")
    if not (cid and csec):
        raise SystemExit("NAVER_CLIENT_ID / NAVER_CLIENT_SECRET are required")

    from app.clients.naver_datalab_client import NaverDataLabClient, NaverDataLabError

    client = NaverDataLabClient(client_id=cid, client_secret=csec)
    events = [e for e in json.load(open(args.events, encoding="utf-8")) if e.get("keyword")]
    yesterday = date.today() - timedelta(days=1)

    out_path = Path(args.out)
    done_path = out_path.with_suffix(out_path.suffix + ".done")
    done: set[str] = set()
    if done_path.exists():
        done = set(done_path.read_text(encoding="utf-8").splitlines())
    csv_exists = out_path.exists()

    csv_fh = open(out_path, "a", newline="", encoding="utf-8")
    writer = csv.writer(csv_fh)
    if not csv_exists:
        writer.writerow(["event_id", "ticker", "keyword", "period", "ratio"])
    done_fh = open(done_path, "a", encoding="utf-8")

    validated = failed = 0
    try:
        for ev in events:
            ticker, kw = ev["ticker"], ev["keyword"]
            event_id = f"{ticker}|{ev['week_date']}"
            tag = f"{event_id}|{kw}"
            if tag in done:
                continue
            move_day = date.fromisoformat(ev["move_day"])
            start = move_day - timedelta(days=args.window_days)
            end = min(move_day + timedelta(days=args.window_days), yesterday)
            if start > end:
                print(f"  ! {tag}: window entirely in the future, skip")
                _mark_done(done_fh, done, tag)
                continue
            try:
                recs = await client.search(
                    keyword_group=ticker, keywords=[kw],
                    start_date=start.isoformat(), end_date=end.isoformat(), time_unit="date",
                )
            except NaverDataLabError as exc:
                print(f"  ! {tag}: {exc}")  # quota/network -> retry next run
                failed += 1
                continue
            writer.writerows((event_id, ticker, kw, r.period, r.ratio) for r in recs)
            nz = sum(1 for r in recs if r.ratio > 0)
            print(f"{event_id}  {kw}: {len(recs)} days, {nz} nonzero  [{start}..{end}]")
            validated += 1
            _mark_done(done_fh, done, tag)
    finally:
        csv_fh.close()
        done_fh.close()

    print(f"\n[out] appended to {args.out}  (resume sidecar: {done_path.name})")
    print(f"this run: {validated} events collected, {failed} failed (quota/network - re-run to retry)")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
