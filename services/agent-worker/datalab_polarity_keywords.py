"""Draft polarity-tagged DataLab keywords (demand vs risk) for a stock — Stage 1.

This is the keyword-generator boundary tool (NOT a collector): it may call Gemini,
writes a review artifact, and only applies to the DB when explicitly approved. It
exists so the DataLab analyzer can score demand-up as positive and risk-up as
negative (see migration 018 polarity column), instead of "any search rise = good".

Polarity is a classification of WHAT a keyword searches for (e.g. "삼성전자 신제품"
= demand intent, "삼성전자 리콜" = risk intent) — NOT a bullish/bearish judgment of
the stock. Reuses the Gemini call + DB helpers from keyword_generator.py.

Usage:
    python datalab_polarity_keywords.py --ticker 005930            # draft → review file
    python datalab_polarity_keywords.py --ticker 005930 --apply    # apply reviewed draft
    python datalab_polarity_keywords.py --ticker 005930 --review-file <path> --apply
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

from keyword_generator import call_gemini, fetch_stock, load_env, parse_dsn

AGENT_ROOT = Path(__file__).resolve().parent
REVIEW_DIR = AGENT_ROOT / "keyword_reviews"
_POLARITIES = {"demand", "risk", "neutral"}


def build_prompt(stock: dict[str, Any]) -> str:
    names = [stock.get(k) for k in ("name", "corp_name", "corp_name_eng", "stock_name")]
    names = [n for n in names if n]
    return f"""
You are drafting NAVER DataLab search categories with POLARITY tags for a Korean stock.

Return JSON only. No markdown.

Stock:
- ticker: {stock["ticker"]}
- names: {", ".join(names)}
- market: {stock.get("market") or ""}
- sector: {stock.get("sector") or ""}

Goal:
- Generate categories whose KEYWORDS are tagged with a polarity describing what the
  search expresses — NOT a stock judgment, NOT investment advice, NOT a price view.
- polarity meanings:
  - "demand"  : interest/demand intent (신제품, 출시, 구매, 예약, 신기술, 채용 등 긍정 수요 신호)
  - "risk"    : trouble/concern intent (리콜, 불매, 논란, 결함, 사고, 소송, 화재, 실적쇼크, 횡령 등 악재 검색)
  - "neutral" : ambiguous brand/name search with no clear direction

Rules:
- category_name: stable UPPERCASE_SNAKE_CASE, prefer ticker prefix for stock-specific themes.
- keyword_group equals category_name.
- Generate 3 to 5 categories. Include AT LEAST ONE category of demand keywords AND
  one category of risk keywords (e.g. "{stock['ticker']}_RISK").
- Each category: 2 to 6 Korean keywords, each with text + polarity.
- Risk keywords MUST be realistic Korean search phrases people type during bad news.
- linked_stocks: this ticker with weight 1.0.

JSON shape:
{{
  "ticker": "...",
  "stock_name": "...",
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
    draft["ticker"] = str(draft.get("ticker") or ticker)
    categories = draft.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ValueError("Gemini response must include non-empty categories.")
    seen_cat: set[str] = set()
    has_risk = False
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
                has_risk = has_risk or polarity == "risk"
        if not cleaned:
            raise ValueError(f"{name} has no usable keywords.")
        category["keywords"] = cleaned
    if not has_risk:
        raise ValueError("Draft must include at least one risk-polarity keyword.")
    return draft


def write_review(draft: dict[str, Any], ticker: str) -> Path:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REVIEW_DIR / f"polarity_{ticker}_{stamp}.json"
    path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


async def apply_draft(draft: dict[str, Any]) -> None:
    conn = await asyncpg.connect(**parse_dsn(os.environ["DATABASE_URL"]))
    try:
        async with conn.transaction():
            stock_id = await conn.fetchval(
                "SELECT id FROM stocks WHERE ticker = $1 AND is_active = TRUE", draft["ticker"]
            )
            if stock_id is None:
                raise RuntimeError(f"Active stock not found for ticker {draft['ticker']}.")
            for category in draft["categories"]:
                category_id = await conn.fetchval(
                    """
                    INSERT INTO datalab_categories (name, sector, description, is_active)
                    VALUES ($1, $2, $3, TRUE)
                    ON CONFLICT (name) DO UPDATE
                    SET sector = EXCLUDED.sector, description = EXCLUDED.description,
                        is_active = TRUE, updated_at = NOW()
                    RETURNING id
                    """,
                    category["category_name"], category.get("sector"), category.get("description"),
                )
                await conn.execute(
                    """
                    INSERT INTO datalab_category_stocks (category_id, stock_id, weight)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (category_id, stock_id) DO UPDATE SET weight = EXCLUDED.weight
                    """,
                    category_id, stock_id,
                    float(category.get("linked_stocks", [{"weight": 1.0}])[0].get("weight", 1.0)),
                )
                for kw in category["keywords"]:
                    await conn.execute(
                        """
                        INSERT INTO datalab_category_keywords (
                            category_id, keyword, keyword_group, source, polarity, is_active
                        )
                        VALUES ($1, $2, $3, 'gemini_reviewed', $4, TRUE)
                        ON CONFLICT (category_id, keyword) DO UPDATE
                        SET keyword_group = EXCLUDED.keyword_group, source = EXCLUDED.source,
                            polarity = EXCLUDED.polarity, is_active = TRUE, updated_at = NOW()
                        """,
                        category_id, kw["text"], category["keyword_group"], kw["polarity"],
                    )
                category["category_id"] = category_id
    finally:
        await conn.close()


async def main() -> None:
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--apply", action="store_true", help="Apply the reviewed draft to the DB.")
    parser.add_argument("--review-file", help="Apply an existing reviewed JSON instead of calling Gemini.")
    args = parser.parse_args()

    if args.review_file:
        draft = validate_draft(json.loads(Path(args.review_file).read_text(encoding="utf-8")), args.ticker)
        review_path = Path(args.review_file)
    else:
        stock = await fetch_stock(args.ticker)
        draft = validate_draft(call_gemini(build_prompt(stock)), args.ticker)
        review_path = write_review(draft, args.ticker)

    n_demand = sum(1 for c in draft["categories"] for k in c["keywords"] if k["polarity"] == "demand")
    n_risk = sum(1 for c in draft["categories"] for k in c["keywords"] if k["polarity"] == "risk")
    print(f"review_file={review_path}")
    print(f"categories={len(draft['categories'])} demand_kw={n_demand} risk_kw={n_risk}")
    if args.apply:
        await apply_draft(draft)
        print("applied=true")
    else:
        print("applied=false (review the file, then re-run with --apply --review-file <path>)")


if __name__ == "__main__":
    asyncio.run(main())
