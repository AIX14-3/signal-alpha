"""Generate reviewed DataLab keyword categories for a stock.

Stage 1 only: this script may call Gemini, writes a review artifact, and can
apply approved keywords to the registry/DB. Patent/DataLab collectors must not
import or call this module.
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
from urllib.parse import unquote
from urllib.request import Request, urlopen

import asyncpg  # type: ignore[import]

ROOT = Path(__file__).resolve().parents[2]
AGENT_ROOT = Path(__file__).resolve().parent
REGISTRY_PATH = AGENT_ROOT / "category_registry.json"
REVIEW_DIR = AGENT_ROOT / "keyword_reviews"


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_dsn(dsn: str) -> dict[str, Any]:
    match = re.match(
        r"^postgres(?:ql)?://(?P<user>[^:]+):(?P<password>.*)@"
        r"(?P<host>[^:/@]+):(?P<port>\d+)/(?P<db>[^?]+)",
        dsn,
    )
    if not match:
        raise ValueError("Could not parse DATABASE_URL")
    return {
        "user": unquote(match.group("user")),
        "password": unquote(match.group("password")),
        "host": match.group("host"),
        "port": int(match.group("port")),
        "database": match.group("db"),
    }


async def fetch_stock(ticker: str) -> dict[str, Any]:
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required.")
    conn = await asyncpg.connect(**parse_dsn(dsn))
    try:
        row = await conn.fetchrow(
            """
            SELECT
                s.id,
                s.ticker,
                s.name,
                s.market,
                s.sector,
                d.corp_name,
                d.corp_name_eng,
                d.stock_name
            FROM stocks s
            LEFT JOIN dart_corp_codes d ON d.stock_id = s.id AND d.is_active = TRUE
            WHERE s.ticker = $1
              AND s.is_active = TRUE
            """,
            ticker,
        )
    finally:
        await conn.close()
    if row is None:
        raise RuntimeError(f"Active stock not found for ticker {ticker}.")
    return dict(row)


def build_prompt(stock: dict[str, Any]) -> str:
    names = [
        stock.get("name"),
        stock.get("corp_name"),
        stock.get("corp_name_eng"),
        stock.get("stock_name"),
    ]
    names = [name for name in names if name]
    return f"""
You are drafting NAVER DataLab search categories for a Korean stock.

Return JSON only. Do not include markdown.

Stock:
- ticker: {stock["ticker"]}
- names: {", ".join(names)}
- market: {stock.get("market") or ""}
- sector: {stock.get("sector") or ""}

Goal:
- Generate DataLab categories and keywords that can be collected as raw search
  trend evidence.
- The output is a draft for human review.
- Do not judge whether any keyword is bullish or bearish.
- Do not predict prices, score signals, or write investment advice.

Rules:
- category_name must be stable uppercase snake case.
- Prefer category names prefixed with the ticker when the theme is stock-specific.
- keyword_group should equal category_name.
- Generate 3 to 6 categories.
- Each category must have 3 to 5 Korean search keywords.
- Include one brand/company category.
- Include product, business, technology, regulation, risk, or demand themes when relevant.
- Avoid generic market-wide words that are not meaningfully tied to the stock.
- linked_stocks must include only this ticker with weight 1.0 unless a category
  is clearly cross-stock, in which case still include this ticker.

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
      "keywords": ["...", "..."],
      "linked_stocks": [{{"ticker": "...", "weight": 1.0}}],
      "rationale": "..."
    }}
  ]
}}
""".strip()


def call_gemini(prompt: str) -> dict[str, Any]:
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for keyword generation.")
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "responseMimeType": "application/json",
        },
    }
    req = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(req, timeout=60) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    text = payload["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def validate_draft(draft: dict[str, Any], ticker: str) -> dict[str, Any]:
    draft["ticker"] = str(draft.get("ticker") or ticker)
    categories = draft.get("categories")
    if not isinstance(categories, list) or not categories:
        raise ValueError("Gemini response must include non-empty categories.")
    seen: set[str] = set()
    for category in categories:
        name = str(category.get("category_name") or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9_]{3,100}", name):
            raise ValueError(f"Invalid category_name: {name!r}")
        if name in seen:
            raise ValueError(f"Duplicate category_name: {name}")
        seen.add(name)
        category["category_name"] = name
        category["keyword_group"] = str(category.get("keyword_group") or name).strip().upper()
        keywords = category.get("keywords")
        if not isinstance(keywords, list) or not 1 <= len(keywords) <= 8:
            raise ValueError(f"{name} must include 1 to 8 keywords.")
        cleaned: list[str] = []
        for keyword in keywords:
            value = str(keyword).strip()
            if value and value not in cleaned:
                cleaned.append(value)
        if not cleaned:
            raise ValueError(f"{name} has no usable keywords.")
        category["keywords"] = cleaned
    return draft


def write_review(draft: dict[str, Any], ticker: str) -> Path:
    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = REVIEW_DIR / f"{ticker}_{stamp}.json"
    path.write_text(json.dumps(draft, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


async def apply_draft(draft: dict[str, Any]) -> None:
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required.")
    conn = await asyncpg.connect(**parse_dsn(dsn))
    try:
        async with conn.transaction():
            stock_id = await conn.fetchval(
                "SELECT id FROM stocks WHERE ticker = $1 AND is_active = TRUE",
                draft["ticker"],
            )
            if stock_id is None:
                raise RuntimeError(f"Active stock not found for ticker {draft['ticker']}.")
            for category in draft["categories"]:
                category_id = await conn.fetchval(
                    """
                    INSERT INTO datalab_categories (name, sector, description, is_active)
                    VALUES ($1, $2, $3, TRUE)
                    ON CONFLICT (name) DO UPDATE
                    SET sector = EXCLUDED.sector,
                        description = EXCLUDED.description,
                        is_active = TRUE,
                        updated_at = NOW()
                    RETURNING id
                    """,
                    category["category_name"],
                    category.get("sector"),
                    category.get("description"),
                )
                await conn.execute(
                    """
                    INSERT INTO datalab_category_stocks (category_id, stock_id, weight)
                    VALUES ($1, $2, $3)
                    ON CONFLICT (category_id, stock_id) DO UPDATE
                    SET weight = EXCLUDED.weight
                    """,
                    category_id,
                    stock_id,
                    float(category.get("linked_stocks", [{"weight": 1.0}])[0].get("weight", 1.0)),
                )
                for keyword in category["keywords"]:
                    await conn.execute(
                        """
                        INSERT INTO datalab_category_keywords (
                            category_id, keyword, keyword_group, source, is_active
                        )
                        VALUES ($1, $2, $3, 'gemini_reviewed', TRUE)
                        ON CONFLICT (category_id, keyword) DO UPDATE
                        SET keyword_group = EXCLUDED.keyword_group,
                            source = EXCLUDED.source,
                            is_active = TRUE,
                            updated_at = NOW()
                        """,
                        category_id,
                        keyword,
                        category["keyword_group"],
                    )
                category["category_id"] = category_id
    finally:
        await conn.close()
    update_registry(draft)


def update_registry(draft: dict[str, Any]) -> None:
    existing: list[dict[str, Any]] = []
    if REGISTRY_PATH.exists():
        existing = json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    by_name = {item["category_name"]: item for item in existing}
    for category in draft["categories"]:
        by_name[category["category_name"]] = {
            "category_id": by_name.get(category["category_name"], {}).get("category_id"),
            "category_name": category["category_name"],
            "keyword_group": category["keyword_group"],
            "keywords": category["keywords"],
        }
    REGISTRY_PATH.write_text(
        json.dumps(list(by_name.values()), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


async def main() -> None:
    load_env()
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--apply", action="store_true", help="Apply reviewed draft to DB and registry.")
    parser.add_argument("--review-file", help="Apply an existing reviewed JSON file instead of calling Gemini.")
    args = parser.parse_args()

    if args.review_file:
        draft = validate_draft(json.loads(Path(args.review_file).read_text(encoding="utf-8")), args.ticker)
        review_path = Path(args.review_file)
    else:
        stock = await fetch_stock(args.ticker)
        draft = validate_draft(call_gemini(build_prompt(stock)), args.ticker)
        review_path = write_review(draft, args.ticker)

    print(f"review_file={review_path}")
    print(f"categories={len(draft['categories'])}")
    if args.apply:
        await apply_draft(draft)
        print("applied=true")
    else:
        print("applied=false")


if __name__ == "__main__":
    asyncio.run(main())
