"""Daily DataLab keyword pipeline — generate, validate, and auto-apply by tier.

Runs BEFORE the daily DataLab collection so that fresh keywords are available the same
day. For every active stock it:

  1. drafts new event-driven keywords from recent DART disclosures (refresh drafter),
  2. drops keywords already mapped,
  3. validates each against real Naver search volume (the search-volume gate),
  4. auto-applies by tier — approved keywords go active immediately (no stale wait),
     pending (borderline) keywords are stored for the admin page, rejected ones dropped.

Boundary: this is the keyword-generator track (may call Gemini + read Naver trends,
writes keyword tables). It does NOT run collectors — the workflow runs run_collectors.py
as a separate step afterward, and that step only reads is_active=TRUE keywords, so pending
keywords are naturally excluded until an admin approves them.

Human review is preserved for the borderline minority (pending). The confident majority
flows automatically, so collected data never waits a week for a manual --apply.

Usage:
    python daily_keyword_pipeline.py                 # all active stocks
    python daily_keyword_pipeline.py --ticker 005930 # one stock
    python daily_keyword_pipeline.py --limit 10 --max-calls 300
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import asyncpg  # type: ignore[import]

sys.path.insert(0, str(Path(__file__).parent))

from datalab_keyword_validator import (  # noqa: E402
    MAX_VALIDATION_CALLS,
    build_client,
    validate_draft_volume,
)
from datalab_polarity_keywords import apply_draft  # noqa: E402
from datalab_polarity_refresh import (  # noqa: E402
    DEFAULT_DAYS,
    build_prompt,
    drop_existing,
    fetch_existing_keywords,
    validate_draft,
    write_review,
)
from keyword_gen_common import call_gemini, fetch_stock, load_env, parse_dsn  # noqa: E402
from news_event_source import gather_events  # noqa: E402


async def _active_tickers(limit: int | None) -> list[str]:
    conn = await asyncpg.connect(**parse_dsn(os.environ["DATABASE_URL"]))
    try:
        rows = await conn.fetch(
            """
            SELECT ticker FROM stocks
            WHERE is_active = TRUE
            ORDER BY id
            """
            + (" LIMIT $1" if limit else ""),
            *([limit] if limit else []),
        )
    finally:
        await conn.close()
    return [r["ticker"] for r in rows]


async def _process_ticker(
    ticker: str, *, days: int, source: str, ground: bool, client, calls_remaining: int
) -> dict[str, int]:
    """Draft → validate → auto-apply one ticker. Returns a per-tier summary."""
    empty = {"auto": 0, "review": 0, "reject": 0, "capped": 0, "calls": 0, "spike": 0}
    stock = await fetch_stock(ticker)
    existing = await fetch_existing_keywords(stock["id"])
    existing_set = {e["keyword"] for e in existing}

    events = await gather_events(stock, days=days, source=source, ground=ground)
    if not events:
        return empty
    draft = validate_draft(call_gemini(build_prompt(stock, events, existing)), ticker)
    draft = drop_existing(draft, existing_set)
    if not draft["categories"]:
        return empty

    draft, summary = await validate_draft_volume(draft, client, max_calls=max(0, calls_remaining))
    write_review(draft, ticker)
    if draft["categories"]:
        await apply_draft(draft, manual=False)
    return summary


async def main() -> None:
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", help="Limit to one ticker (default: all active stocks).")
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="DART disclosure lookback window.")
    parser.add_argument("--limit", type=int, help="Max number of stocks to process.")
    parser.add_argument(
        "--max-calls",
        type=int,
        default=MAX_VALIDATION_CALLS,
        help="Total Naver validation calls allowed across this run (quota guard).",
    )
    parser.add_argument(
        "--source",
        choices=("news", "dart", "both"),
        default="both",
        help="Event source for keyword drafting (default: both).",
    )
    ground_default = os.getenv("KEYWORD_GROUNDING", "on").lower() != "off"
    ground_group = parser.add_mutually_exclusive_group()
    ground_group.add_argument(
        "--ground", dest="ground", action="store_true", default=ground_default,
        help="Deepen news headlines via Gemini Google-Search grounding (default).",
    )
    ground_group.add_argument(
        "--no-ground", dest="ground", action="store_false",
        help="Skip grounding enrichment.",
    )
    args = parser.parse_args()

    tickers = [args.ticker] if args.ticker else await _active_tickers(args.limit)
    client = build_client()
    totals = {"auto": 0, "review": 0, "reject": 0, "capped": 0, "calls": 0, "spike": 0}

    print(
        f"DAILY KEYWORD PIPELINE — {len(tickers)} stock(s), max_calls={args.max_calls}, "
        f"source={args.source}, ground={'on' if args.ground else 'off'}"
    )
    print("=" * 60)
    for ticker in tickers:
        remaining = args.max_calls - totals["calls"]
        try:
            summary = await _process_ticker(
                ticker,
                days=args.days,
                source=args.source,
                ground=args.ground,
                client=client,
                calls_remaining=remaining,
            )
        except Exception as exc:  # one bad ticker must not abort the daily batch
            print(f"> {ticker} ERROR {type(exc).__name__}: {exc}")
            continue
        for key in totals:
            totals[key] += summary.get(key, 0)
        print(
            f"> {ticker} auto={summary['auto']} review={summary['review']} "
            f"reject={summary['reject']} capped={summary['capped']}"
        )

    print("=" * 60)
    print(
        f"TOTAL approved(auto)={totals['auto']} pending(review)={totals['review']} "
        f"dropped(reject)={totals['reject']} capped={totals['capped']} "
        f"spike_fasttrack={totals['spike']} naver_calls={totals['calls']}"
    )
    if totals["review"]:
        print(f"NOTE: {totals['review']} borderline keyword(s) await admin approval (review_status='pending').")


if __name__ == "__main__":
    asyncio.run(main())
