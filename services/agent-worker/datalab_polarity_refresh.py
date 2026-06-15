"""Refresh polarity-tagged DataLab keywords from recent DART disclosures — Stage 2.

The bootstrap drafter (``datalab_polarity_keywords.py``) is static: a cutoff-bound
LLM, asked cold, only ever invents the same generic 리콜/불매/논란 keywords. Its value
decays because the keyword list never tracks what is actually happening to a company.

This tool closes that gap. It feeds the company's RECENT DART disclosure titles
(real, dated events — already collected into ``dart_raw_details``) to Gemini and asks
it to discover the NAVER search phrases ordinary people would type in reaction to
those specific events, that are NOT already mapped. That makes the keyword set
event-driven instead of frozen at the model's training cutoff.

Same boundary as the bootstrap drafter: this is the keyword-generator tool, NOT a
collector. It may call Gemini, writes a review artifact, and only touches the DB
when explicitly approved with ``--apply``. It reuses the polarity-aware apply path
from ``datalab_polarity_keywords`` so refreshed keywords flow through one code path.

Usage:
    python datalab_polarity_refresh.py --ticker 005930                  # draft → review file
    python datalab_polarity_refresh.py --ticker 005930 --days 180       # widen the event window
    python datalab_polarity_refresh.py --ticker 005930 --review-file <path> --apply
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import]

from datalab_polarity_keywords import REVIEW_DIR, _POLARITIES, apply_draft
from keyword_generator import call_gemini, fetch_stock, load_env, parse_dsn

DEFAULT_DAYS = 365
MAX_DISCLOSURES = 80


def build_prompt(
    stock: dict[str, Any],
    disclosures: list[dict[str, Any]],
    existing: list[dict[str, Any]],
) -> str:
    names = [stock.get(k) for k in ("name", "corp_name", "corp_name_eng", "stock_name")]
    names = [n for n in names if n]
    event_lines = "\n".join(
        f"- [{d.get('disclosure_type') or '공시'}] {d['report_name']}"
        + (f" (사유: {d['priority_reason']})" if d.get("priority_reason") else "")
        for d in disclosures
    )
    existing_lines = ", ".join(
        f"{e['keyword']}({e['polarity']})" for e in existing
    ) or "(없음)"
    return f"""
You are REFRESHING NAVER DataLab search keywords for a Korean stock from its RECENT
real disclosure events. Return JSON only. No markdown.

Stock:
- ticker: {stock["ticker"]}
- names: {", ".join(names)}
- sector: {stock.get("sector") or ""}

Recent DART disclosure titles (real, dated company events):
{event_lines}

Keywords ALREADY mapped (do NOT repeat any of these):
{existing_lines}

Goal:
- For the events above, generate the NAVER search phrases Korean people would
  actually TYPE when reacting to them — phrases NOT already in the mapped list.
- polarity describes WHAT the search expresses, NOT a stock judgment / price view:
  - "risk"    : trouble/concern intent triggered by bad-news events
    (리콜, 불매, 논란, 결함, 사고, 소송, 화재, 횡령, 유상증자, 감자, 실적쇼크, 상장폐지 등)
  - "demand"  : interest/demand intent triggered by good-news events
    (신제품, 출시, 수주, 계약, 신기술, 예약, 채용 등)
  - "neutral" : ambiguous brand search with no clear direction
- ONLY emit keywords clearly grounded in the events above. If an event is purely
  routine/administrative (정기보고서, 분기보고서 등) and implies no public search
  behavior, emit nothing for it.
- If there are no events worth new keywords, return an empty "categories" list.

Rules:
- category_name: stable UPPERCASE_SNAKE_CASE, prefer a "{stock['ticker']}_RISK" or
  "{stock['ticker']}_DEMAND" theme so refreshed keywords group cleanly.
- keyword_group equals category_name.
- Each category: 1 to 6 Korean keywords, each with text + polarity.
- Korean keywords must be realistic search phrases, not the disclosure title verbatim.
- linked_stocks: this ticker with weight 1.0.

JSON shape:
{{
  "ticker": "...",
  "categories": [
    {{
      "category_name": "...",
      "sector": "...",
      "description": "...",
      "keyword_group": "...",
      "keywords": [{{"text": "...", "polarity": "demand|risk|neutral"}}],
      "linked_stocks": [{{"ticker": "...", "weight": 1.0}}]
    }}
  ]
}}
""".strip()


def validate_draft(draft: dict[str, Any], ticker: str) -> dict[str, Any]:
    """Validate the refresh draft's structure and polarity.

    Unlike the bootstrap drafter this does NOT require a risk keyword: a refresh
    may legitimately surface only demand keywords (good-news events), or nothing
    at all when recent disclosures imply no public search behavior.
    """
    draft["ticker"] = str(draft.get("ticker") or ticker)
    categories = draft.get("categories")
    if not isinstance(categories, list):
        raise ValueError("Gemini response must include a categories list.")
    seen_cat: set[str] = set()
    for category in categories:
        name = str(category.get("category_name") or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9_]{3,100}", name):
            raise ValueError(f"Invalid category_name: {name!r}")
        if name in seen_cat:
            raise ValueError(f"Duplicate category_name: {name}")
        seen_cat.add(name)
        category["category_name"] = name
        category["keyword_group"] = str(category.get("keyword_group") or name).strip().upper()
        keywords = category.get("keywords")
        if not isinstance(keywords, list) or not 1 <= len(keywords) <= 8:
            raise ValueError(f"{name} must include 1 to 8 keywords.")
        cleaned: list[dict[str, str]] = []
        seen_kw: set[str] = set()
        for kw in keywords:
            text = str((kw or {}).get("text") or "").strip()
            polarity = str((kw or {}).get("polarity") or "demand").strip().lower()
            if polarity not in _POLARITIES:
                raise ValueError(f"{name}: invalid polarity {polarity!r}")
            if text and text not in seen_kw:
                seen_kw.add(text)
                cleaned.append({"text": text, "polarity": polarity})
        if not cleaned:
            raise ValueError(f"{name} has no usable keywords.")
        category["keywords"] = cleaned
    return draft


def drop_existing(draft: dict[str, Any], existing_keywords: set[str]) -> dict[str, Any]:
    """Remove keywords already mapped and drop any category left empty.

    Belt-and-suspenders: the prompt tells Gemini not to repeat mapped keywords,
    but apply must never silently re-touch existing rows under a new category.
    """
    kept: list[dict[str, Any]] = []
    for category in draft.get("categories", []):
        fresh = [kw for kw in category["keywords"] if kw["text"] not in existing_keywords]
        if fresh:
            category["keywords"] = fresh
            kept.append(category)
    draft["categories"] = kept
    return draft


async def fetch_recent_disclosures(stock_id: int, *, days: int) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(**parse_dsn(os.environ["DATABASE_URL"]))
    try:
        rows = await conn.fetch(
            """
            SELECT report_name, disclosure_type, priority, priority_reason, created_at
            FROM dart_raw_details
            WHERE stock_id = $1
              AND created_at >= NOW() - ($2::int * INTERVAL '1 day')
            ORDER BY priority = 'immediate' DESC, created_at DESC
            LIMIT $3
            """,
            stock_id, days, MAX_DISCLOSURES,
        )
    finally:
        await conn.close()
    return [dict(r) for r in rows]


async def fetch_existing_keywords(stock_id: int) -> list[dict[str, Any]]:
    conn = await asyncpg.connect(**parse_dsn(os.environ["DATABASE_URL"]))
    try:
        rows = await conn.fetch(
            """
            SELECT DISTINCT dck.keyword, dck.polarity
            FROM datalab_category_keywords dck
            JOIN datalab_category_stocks dcs ON dcs.category_id = dck.category_id
            WHERE dcs.stock_id = $1 AND dck.is_active = TRUE
            ORDER BY dck.keyword
            """,
            stock_id,
        )
    finally:
        await conn.close()
    return [dict(r) for r in rows]


def write_review(draft: dict[str, Any], ticker: str) -> Path:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REVIEW_DIR / f"polarity_refresh_{ticker}_{stamp}.json"
    path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


async def main() -> None:
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Disclosure lookback window.")
    parser.add_argument("--apply", action="store_true", help="Apply the reviewed draft to the DB.")
    parser.add_argument("--review-file", help="Apply an existing reviewed JSON instead of calling Gemini.")
    args = parser.parse_args()

    stock = await fetch_stock(args.ticker)
    existing = await fetch_existing_keywords(stock["id"])
    existing_set = {e["keyword"] for e in existing}

    if args.review_file:
        draft = validate_draft(json.loads(Path(args.review_file).read_text(encoding="utf-8")), args.ticker)
        draft = drop_existing(draft, existing_set)
        review_path = Path(args.review_file)
    else:
        disclosures = await fetch_recent_disclosures(stock["id"], days=args.days)
        if not disclosures:
            print(f"review_file=(none) disclosures=0 — no recent DART events in {args.days}d; nothing to refresh.")
            return
        draft = validate_draft(call_gemini(build_prompt(stock, disclosures, existing)), args.ticker)
        draft = drop_existing(draft, existing_set)
        print(f"disclosures={len(disclosures)}")
        if not draft["categories"]:
            print("new_keywords=0 — events implied no new searchable keywords; nothing to apply.")
            return
        review_path = write_review(draft, args.ticker)

    n_demand = sum(1 for c in draft["categories"] for k in c["keywords"] if k["polarity"] == "demand")
    n_risk = sum(1 for c in draft["categories"] for k in c["keywords"] if k["polarity"] == "risk")
    print(f"review_file={review_path}")
    print(f"categories={len(draft['categories'])} new_demand_kw={n_demand} new_risk_kw={n_risk}")
    if args.apply:
        if not draft["categories"]:
            print("applied=false (nothing new to apply)")
            return
        await apply_draft(draft)
        print("applied=true")
    else:
        print("applied=false (review the file, then re-run with --apply --review-file <path>)")


if __name__ == "__main__":
    asyncio.run(main())
