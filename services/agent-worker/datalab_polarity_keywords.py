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
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import asyncpg  # type: ignore[import]

from keyword_gen_common import call_gemini, fetch_stock, load_env, parse_dsn

AGENT_ROOT = Path(__file__).resolve().parent
REVIEW_DIR = AGENT_ROOT / "keyword_reviews"
_POLARITIES = {"demand", "risk", "neutral"}
_DEFAULT_CONFIDENCE = 0.5
# Review files are disposable draft artifacts (gitignored). Each generation prunes
# ones older than this so keyword_reviews/ does not grow unbounded. Env-overridable.
REVIEW_RETENTION_DAYS = int(os.getenv("KEYWORD_REVIEW_RETENTION_DAYS", "7"))


def prune_old_reviews(retention_days: int = REVIEW_RETENTION_DAYS) -> list[str]:
    """Delete review JSON files older than ``retention_days`` (best-effort).

    Cleanup must never break draft generation, so a stat/unlink error on any single
    file is skipped. ``retention_days <= 0`` disables pruning. Returns names removed.
    """
    if retention_days <= 0 or not REVIEW_DIR.exists():
        return []
    cutoff = time.time() - retention_days * 86400
    removed: list[str] = []
    for path in REVIEW_DIR.glob("polarity_*.json"):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                removed.append(path.name)
        except OSError:
            continue
    return removed

# Polarity classifies search *intent*, never a stock judgment. Reject any draft
# whose rationale drifts into buy/sell/target-price advice (mirrors dart/llm.py).
_PROHIBITED_ADVICE = [
    re.compile(r"\bbuy\b", re.IGNORECASE),
    re.compile(r"\bsell\b", re.IGNORECASE),
    re.compile(r"\bhold\b", re.IGNORECASE),
    re.compile(r"\btarget\s*price\b", re.IGNORECASE),
    re.compile(r"매수"),
    re.compile(r"매도"),
    re.compile(r"목표가"),
    re.compile(r"투자\s*추천"),
]


def _clamp_confidence(value: Any) -> float:
    try:
        conf = float(value)
    except (TypeError, ValueError):
        return _DEFAULT_CONFIDENCE
    return max(0.0, min(1.0, conf))


def _check_no_advice(text: str, where: str) -> None:
    for pattern in _PROHIBITED_ADVICE:
        if pattern.search(text):
            raise ValueError(
                f"{where}: rationale에 투자 판단 문구가 포함됨({pattern.pattern!r}). "
                "polarity는 검색 의도 분류이지 매수/매도 추천이 아니다."
            )


def build_prompt(stock: dict[str, Any], today: str) -> str:
    names = [stock.get(k) for k in ("name", "corp_name", "corp_name_eng", "stock_name")]
    names = [n for n in names if n]
    return f"""
You are drafting NAVER DataLab search categories with POLARITY tags for a Korean stock.

Return JSON only. No markdown.

Today's date: {today}. Generate keywords that are CURRENT as of this date.

Stock:
- ticker: {stock["ticker"]}
- names: {", ".join(names)}
- market: {stock.get("market") or ""}
- sector: {stock.get("sector") or ""}

Recency (avoid stale model/year names):
- Do NOT use outdated product/model names, past years, or stale version numbers.
- For product lines with annual generations (Galaxy S/Z, iPhone, 신차 연식 등), infer
  the generation current/upcoming as of {today} from the date — e.g. an annual line
  whose Nth edition shipped in 2024 should be bumped to the edition expected in
  {today[:4]}, not the older one you saw in training.
- If unsure of the exact current model, prefer an evergreen phrase
  (e.g. "삼성전자 신제품", "갤럭시 신제품") over a specific stale model number.

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
- Each category: 2 to 6 Korean keywords, each with text + polarity + confidence + rationale.
- Risk keywords MUST be realistic Korean search phrases people type during bad news.
- confidence: 0.0-1.0, how sure you are of the polarity (ambiguous brand terms → low).
- rationale: one short Korean phrase on WHY this polarity. Describe the search intent
  only — NEVER mention buying/selling, target price, or any investment recommendation.
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
      "keywords": [{{"text": "...", "polarity": "demand|risk|neutral", "confidence": 0.0, "rationale": "..."}}],
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
            rationale = str((kw or {}).get("rationale") or "").strip()
            _check_no_advice(rationale, f"{name}/{text}")
            if text and text not in seen_kw:
                seen_kw.add(text)
                cleaned.append({
                    "text": text,
                    "polarity": polarity,
                    "confidence": _clamp_confidence((kw or {}).get("confidence")),
                    "rationale": rationale,
                })
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
    prune_old_reviews()
    return path


def _status_for(kw: dict[str, Any], manual: bool) -> tuple[str, bool]:
    """Return (review_status, is_active) for a keyword.

    manual apply (human reviewed) -> approved + active for everything.
    auto apply -> derived from the keyword's validation tier (auto->approved/active,
    review->pending/inactive). Missing validation defaults to approved (safe no-op).
    """
    if manual:
        return "approved", True
    from datalab_keyword_validator import TIER_TO_STATUS

    tier = (kw.get("validation") or {}).get("tier", TIER_AUTO_DEFAULT)
    status = TIER_TO_STATUS.get(tier, "approved")
    return status, status == "approved"


# Local fallback so this module need not import the validator just to name a default.
TIER_AUTO_DEFAULT = "auto"


async def apply_draft(draft: dict[str, Any], *, manual: bool = True) -> None:
    """Apply a drafted keyword set to the DB.

    manual=True (default): a human is applying a reviewed draft — every keyword is
      written approved+active, and re-apply re-activates (legacy ``--apply`` behavior).
    manual=False: the automated gate is applying — each keyword's review_status/is_active
      come from its validation tier. On conflict the EXISTING review_status/is_active are
      preserved so a daily run never silently flips an admin's prior approve/reject.
    """
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
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
                    status, is_active = _status_for(kw, manual)
                    val = kw.get("validation") or {}
                    await conn.execute(
                        """
                        INSERT INTO datalab_category_keywords (
                            category_id, keyword, keyword_group, source, polarity,
                            is_active, review_status,
                            validation_active_days, validation_window_days, validation_coverage,
                            validated_at,
                            polarity_source, polarity_confidence, polarity_model,
                            polarity_rationale, polarity_classified_at
                        )
                        VALUES ($1, $2, $3, 'gemini_reviewed', $4,
                                $5, $6,
                                $7, $8, $9,
                                CASE WHEN $7::integer IS NULL THEN NULL ELSE NOW() END,
                                'llm', $10, $11, $12, NOW())
                        ON CONFLICT (category_id, keyword) DO UPDATE
                        SET keyword_group = EXCLUDED.keyword_group, source = EXCLUDED.source,
                            polarity = EXCLUDED.polarity, updated_at = NOW(),
                            is_active = CASE WHEN $13 THEN EXCLUDED.is_active
                                             ELSE datalab_category_keywords.is_active END,
                            review_status = CASE WHEN $13 THEN EXCLUDED.review_status
                                                 ELSE datalab_category_keywords.review_status END,
                            validation_active_days = EXCLUDED.validation_active_days,
                            validation_window_days = EXCLUDED.validation_window_days,
                            validation_coverage = EXCLUDED.validation_coverage,
                            validated_at = EXCLUDED.validated_at,
                            polarity_source = EXCLUDED.polarity_source,
                            polarity_confidence = EXCLUDED.polarity_confidence,
                            polarity_model = EXCLUDED.polarity_model,
                            polarity_rationale = EXCLUDED.polarity_rationale,
                            polarity_classified_at = EXCLUDED.polarity_classified_at
                        """,
                        category_id, kw["text"], category["keyword_group"], kw["polarity"],
                        is_active, status,
                        val.get("active_days"), val.get("window_days"), val.get("coverage"),
                        kw.get("confidence"), model, kw.get("rationale") or None,
                        manual,
                    )
                category["category_id"] = category_id
    finally:
        await conn.close()


async def main() -> None:
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--apply", action="store_true", help="Apply the reviewed draft to the DB.")
    parser.add_argument(
        "--auto-apply",
        action="store_true",
        help="Validate via Naver search volume and apply by tier (approved/pending) "
        "WITHOUT human review. Rejected (no-search) keywords are dropped.",
    )
    parser.add_argument("--review-file", help="Apply an existing reviewed JSON instead of calling Gemini.")
    args = parser.parse_args()

    if args.review_file:
        draft = validate_draft(json.loads(Path(args.review_file).read_text(encoding="utf-8")), args.ticker)
        review_path = Path(args.review_file)
    else:
        stock = await fetch_stock(args.ticker)
        today = datetime.now().strftime("%Y-%m-%d")
        draft = validate_draft(call_gemini(build_prompt(stock, today)), args.ticker)
        if args.auto_apply:
            from datalab_keyword_validator import build_client, validate_draft_volume

            draft, summary = await validate_draft_volume(draft, build_client())
            print(
                f"validation auto={summary['auto']} review={summary['review']} "
                f"reject={summary['reject']} capped={summary['capped']} calls={summary['calls']}"
            )
        review_path = write_review(draft, args.ticker)

    n_demand = sum(1 for c in draft["categories"] for k in c["keywords"] if k["polarity"] == "demand")
    n_risk = sum(1 for c in draft["categories"] for k in c["keywords"] if k["polarity"] == "risk")
    print(f"review_file={review_path}")
    print(f"categories={len(draft['categories'])} demand_kw={n_demand} risk_kw={n_risk}")
    if args.auto_apply:
        if not draft["categories"]:
            print("applied=false (all keywords rejected by the search-volume gate)")
            return
        await apply_draft(draft, manual=False)
        print("applied=auto (approved->active; pending->held for admin review)")
    elif args.apply:
        await apply_draft(draft)
        print("applied=true")
    else:
        print("applied=false (review the file, then re-run with --apply --review-file <path>)")


if __name__ == "__main__":
    asyncio.run(main())
