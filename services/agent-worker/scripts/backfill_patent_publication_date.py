"""Backfill ``patent_raw_details.publication_date`` from ``extra_payload``.

특허 공개일(publication date, 출원 후 ~18개월)은 지금까지 1급 컬럼이 아니라
``extra_payload`` JSON 안에만 있었다 (KIPRIS=``OpeningDate``, BigQuery(Google
Patents)=``publication_date``). 마이그레이션
``20260707_1000_patent_publication_date.sql`` 이 ``publication_date date`` 컬럼을
추가한 뒤, 이 스크립트가 **이미 적재된 행**의 공개일을 extra_payload 에서 파싱해
채운다. 공개일을 못 찾으면 NULL 로 남긴다(분석기가 application_date 로 폴백).

수집기(``app/collectors/patent/__init__.py``)는 앞으로 신규 행에 공개일을 직접
기록하므로, 이 스크립트는 **일회성**(과거 155k 행 소급 적용)이다. 멱등하다 —
이미 채워진 행은 ``publication_date IS NULL`` 필터로 건너뛴다.

    # 미리보기(쓰기 없음)
    uv run python scripts/backfill_patent_publication_date.py --dry-run
    # 소규모 스모크
    uv run python scripts/backfill_patent_publication_date.py --limit 200
    # 전체 적용
    uv run python scripts/backfill_patent_publication_date.py

``.env`` 의 ``DATABASE_URL`` (수집 DB) 이 필요하다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Any

# Make ``app``/data-access importable when run as ``python scripts/...``.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# extra_payload 안에서 공개일이 실릴 수 있는 키(소스별 원필드명). 우선순위 순.
_PUB_DATE_KEYS = ("publication_date", "OpeningDate", "openDate", "open_date")


def _parse_date(value: Any) -> date | None:
    """YYYYMMDD 또는 ISO 문자열을 date 로. 미상/파싱실패는 None."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        try:
            return datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(text[:10]).date()
    except ValueError:
        return None


def _extract_publication_date(extra_payload: Any) -> date | None:
    """extra_payload(jsonb; str 또는 dict)에서 공개일을 뽑는다."""
    payload = extra_payload
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (json.JSONDecodeError, TypeError):
            return None
    if not isinstance(payload, dict):
        return None
    for key in _PUB_DATE_KEYS:
        if key in payload:
            parsed = _parse_date(payload[key])
            if parsed is not None:
                return parsed
    return None


async def _run(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Backfill patent_raw_details.publication_date from extra_payload"
    )
    parser.add_argument("--dry-run", action="store_true", help="report only, no DB writes")
    parser.add_argument("--limit", type=int, default=None, help="cap rows scanned (smoke test)")
    parser.add_argument("--batch-size", type=int, default=1000, help="rows per UPDATE batch")
    args = parser.parse_args(argv)

    try:  # repo / service .env supplies DATABASE_URL
        from dotenv import find_dotenv, load_dotenv

        load_dotenv(find_dotenv(usecwd=True))
    except ImportError:
        pass

    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise SystemExit("DATABASE_URL is required (set it in .env).")

    from signal_alpha_data_access import DatabaseSettings, create_pool

    pool = await create_pool(DatabaseSettings(database_url=database_url))

    scanned = 0
    filled = 0
    unresolved = 0
    try:
        async with pool.acquire() as conn:
            query = (
                "SELECT raw_document_id, extra_payload FROM patent_raw_details "
                "WHERE publication_date IS NULL ORDER BY raw_document_id"
            )
            if args.limit is not None:
                query += f" LIMIT {int(args.limit)}"
            rows = await conn.fetch(query)

        updates: list[tuple[int, date]] = []
        for row in rows:
            scanned += 1
            pub = _extract_publication_date(row["extra_payload"])
            if pub is None:
                unresolved += 1
                continue
            updates.append((row["raw_document_id"], pub))

        print(
            f"scanned={scanned} resolvable={len(updates)} unresolved(NULL 유지)={unresolved}"
        )

        if args.dry_run:
            for rid, pub in updates[:10]:
                print(f"  would set raw_document_id={rid} publication_date={pub.isoformat()}")
            if len(updates) > 10:
                print(f"  ... (+{len(updates) - 10} more)")
            print("[dry-run] no DB writes")
            return 0

        for i in range(0, len(updates), args.batch_size):
            batch = updates[i : i + args.batch_size]
            async with pool.acquire() as conn:
                await conn.executemany(
                    "UPDATE patent_raw_details SET publication_date = $2 "
                    "WHERE raw_document_id = $1",
                    batch,
                )
            filled += len(batch)
            print(f"  updated {filled}/{len(updates)}")
    finally:
        await pool.close()

    print(f"[done] filled={filled} unresolved={unresolved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
