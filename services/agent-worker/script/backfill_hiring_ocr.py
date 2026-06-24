"""ENRICH_HIRING OCR 백필 러너 — 과거에 정규화된 채용 공고의 포스터 이미지를 일괄 OCR.

큐 기반 ENRICH_HIRING 은 NORMALIZE_HIRING 이 *새로* 정규화한 행에만 걸린다. 이미 적재된
과거 행(hiring_raw_details)을 채우려면 이 스크립트가 ``ocr_status='pending'`` 전역 sweep 을
배치로 돌린다. 핵심 로직은 검증된 ``HiringSkillEnricher`` 그대로 재사용한다.

설계(안전장치):
  - 멱등 — ``ocr_status='pending'`` 행만 인입. 처리되면 success/failed/skipped 로 전이해
    다음 sweep 에서 자연 제외(재실행은 no-op). image_urls 없는 과거 행은 skipped 로 전이돼
    매번 다시 안 긁힌다(Phase 0 이전 데이터 보호).
  - 무한루프 가드 — 배치가 행을 받고도 한 건도 전이 못 시키면(스키마 미적용/락 의심) 중단.
  - rate-limit — 배치 사이 sleep 으로 DB/CPU 독점 방지(``--sleep``).
  - ``--retry-failed`` — 일시 실패(다운로드 타임아웃 등) failed→pending 리셋 후 재시도.
  - ``--dry-run`` — pending 건수만 출력(처리 안 함).

전제: 워커 런타임에 tesseract(kor+eng) 설치(#428) + 대상 DB 에 migration 028 적용.
      미충족 시 enrich 는 graceful degrade(전부 failed/skipped) — 죽지는 않는다.

USAGE
    uv run python script/backfill_hiring_ocr.py --batch-size 100 --sleep 0.5
    uv run python script/backfill_hiring_ocr.py --dry-run
    uv run python script/backfill_hiring_ocr.py --retry-failed
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Any, Awaitable, Callable

import asyncpg
from dotenv import load_dotenv

_PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))
load_dotenv(_PROJECT_ROOT.parent.parent / ".env")

from app.enrichment.hiring_skills import HiringSkillEnricher  # noqa: E402

logger = logging.getLogger("backfill_hiring_ocr")

_DSN = os.getenv("DATABASE_URL") or (
    f"postgresql://{os.getenv('DB_USER', 'postgres')}:{os.getenv('DB_PASSWORD', 'postgres')}"
    f"@{os.getenv('DB_HOST', 'localhost')}:{int(os.getenv('DB_PORT', '5432'))}"
    f"/{os.getenv('DB_NAME', 'signal_alpha')}"
)


async def run_backfill(
    repository: Any,
    ocr: Any,
    *,
    batch_size: int = 100,
    max_batches: int | None = None,
    sleep_s: float = 0.5,
    sleeper: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> dict[str, int]:
    """pending 행을 batch_size 청크로 소진할 때까지 HiringSkillEnricher 를 반복 실행.

    각 배치의 raw_document_id 를 직접 뽑아(seen 추적) 그 id 들로 스코핑한 enricher 를 돌린다.
    정상 동작 시 처리된 행은 pending 에서 빠져 다음 배치에 안 잡힌다. 새 id 가 하나도 없는
    배치가 나오면(전이 실패로 같은 행이 되돌아옴 — 028 미적용/락 의심) **진척 없음**으로 보고
    중단해 무한루프를 막는다. ``sleeper`` 는 테스트 주입용(rate-limit sleep 대체).
    """
    totals = {"batches": 0, "total": 0, "success": 0, "failed": 0, "skipped": 0}
    seen: set[int] = set()
    while max_batches is None or totals["batches"] < max_batches:
        rows = await repository.list_unenriched_hiring_details(
            limit=batch_size, raw_document_ids=None
        )
        if not rows:
            break  # pending 소진
        batch_ids = [int(r["raw_document_id"]) for r in rows]
        if seen.issuperset(batch_ids):
            logger.warning(
                "진척 없음(새 id 0건, total=%d) — 중단. 028 미적용/락 의심.", len(batch_ids)
            )
            break
        seen.update(batch_ids)
        enricher = HiringSkillEnricher(
            repository, ocr, batch_limit=batch_size, raw_document_ids=batch_ids
        )
        stats = await enricher.run()
        for key in ("total", "success", "failed", "skipped"):
            totals[key] += stats[key]
        totals["batches"] += 1
        logger.info(
            "batch %d: total=%d success=%d failed=%d skipped=%d",
            totals["batches"], stats["total"], stats["success"],
            stats["failed"], stats["skipped"],
        )
        if sleep_s:
            await sleeper(sleep_s)
    return totals


async def _count_pending(conn: Any, status: str = "pending") -> int:
    return int(await conn.fetchval(
        "SELECT count(*) FROM hiring_raw_details WHERE ocr_status = $1", status
    ))


async def _reset_failed(conn: Any) -> int:
    row = await conn.fetchval(
        "WITH u AS (UPDATE hiring_raw_details SET ocr_status='pending' "
        "WHERE ocr_status='failed' RETURNING 1) SELECT count(*) FROM u"
    )
    return int(row or 0)


async def main() -> None:
    # Windows 콘솔 기본 cp949 는 로그의 한글 출력 시 깨진다 → UTF-8 강제.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    logging.basicConfig(
        level=logging.INFO, format="[%(asctime)s] %(levelname)-7s %(message)s"
    )
    parser = argparse.ArgumentParser(description="ENRICH_HIRING OCR 백필 러너 (#390)")
    parser.add_argument("--batch-size", type=int, default=100, help="배치당 행 수")
    parser.add_argument("--max-batches", type=int, default=None, help="최대 배치 수(기본 무제한)")
    parser.add_argument("--sleep", type=float, default=0.5, help="배치 사이 슬립(초)")
    parser.add_argument("--lang", default="kor+eng", help="Tesseract 언어")
    parser.add_argument("--retry-failed", action="store_true", help="failed→pending 리셋 후 재시도")
    parser.add_argument("--dry-run", action="store_true", help="pending 건수만 출력")
    parser.add_argument("--database-url", default=None, help="DATABASE_URL 직접 지정")
    args = parser.parse_args()

    conn = await asyncpg.connect(args.database_url or _DSN)
    try:
        if args.dry_run:
            pending = await _count_pending(conn, "pending")
            failed = await _count_pending(conn, "failed")
            logger.info("[dry-run] pending=%d failed=%d (실제 처리 안 함)", pending, failed)
            return

        if args.retry_failed:
            reset = await _reset_failed(conn)
            logger.info("failed→pending 리셋: %d건", reset)

        # 무거운/네이티브 의존(pytesseract·PIL·tesseract 바이너리)은 처리기 안에서 지연 import.
        from app.enrichment.tesseract_ocr import TesseractOCRProcessor
        from signal_alpha_data_access.repositories import RawDetailRepository

        ocr = TesseractOCRProcessor(lang=args.lang)
        repository = RawDetailRepository(conn)
        totals = await run_backfill(
            repository, ocr,
            batch_size=args.batch_size,
            max_batches=args.max_batches,
            sleep_s=args.sleep,
        )
        logger.info(
            "백필 완료 — 배치 %d / 처리 %d (success=%d failed=%d skipped=%d)",
            totals["batches"], totals["total"], totals["success"],
            totals["failed"], totals["skipped"],
        )
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
