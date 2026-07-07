"""Patent collector — source_type='PATENT', source_name='KIPRIS'."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime
from typing import Any

import asyncpg  # type: ignore[import]
import asyncpg.exceptions  # type: ignore[import]

from app.clients.kipris_client import KiprisClient, KiprisPatentRecord, _default_end_date, _default_start_date
from app.collectors.patent.applicant_aliases import build_applicant_aliases as _build_applicant_aliases
from app.collectors.patent.application_no import canonicalize_application_no
from app.observability import calculate_run_status as _run_status
from app.utils.hash_utils import make_source_hash
from signal_alpha_data_access.repositories import ProcessingQueueRepository

logger = logging.getLogger(__name__)

SOURCE_TYPE = "PATENT"
SOURCE_NAME = "KIPRIS"
TASK_TYPE = "NORMALIZE_PATENT"

# 크로스소스 dedup 우선순위(높을수록 우선). 같은 canonical 특허가 두 소스에 겹치면
# source_hash UNIQUE 로 1행만 남는데, 사용자 정책 = **KIPRIS 우선**이라 나중에 온
# KIPRIS 가 기존 GOOGLE_PATENTS 행을 덮어쓴다(_promote_source_if_outranked).
_SOURCE_PRIORITY: dict[str, int] = {"KIPRIS": 2, "GOOGLE_PATENTS": 1}


class PatentApiError(RuntimeError):
    pass


class PatentCollector:
    """Collects patents from KIPRISPlus and stores them per DB contract."""

    def __init__(
        self,
        *,
        pool: Any,
        client: KiprisClient,
        collector_ver: str | None = None,
    ) -> None:
        self._pool = pool
        self._client = client
        self._collector_ver = collector_ver or os.getenv("COLLECTOR_VERSION", "1.0")

    async def run(
        self,
        *,
        stock_id: int,
        stock_code: str,
        stock_name: str | None = None,
        applicant_name: str | None = None,
        applicant_names: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
    ) -> dict[str, Any]:
        start = start_date or _default_start_date()
        end = end_date or _default_end_date()
        applicant_aliases = _build_applicant_aliases(
            stock_name=stock_name,
            applicant_name=applicant_name,
            applicant_names=applicant_names,
        )

        async with self._pool.acquire() as conn:
            run_id = await _create_run(conn)
            if not applicant_aliases:
                stock_meta = await _fetch_stock_alias_seeds(conn, stock_id=stock_id, stock_code=stock_code)
                applicant_aliases = _build_applicant_aliases(
                    stock_name=stock_meta.get("stock_name"),
                    applicant_name=None,
                    applicant_names=stock_meta.get("applicant_names", []),
                )
            known_categories = await _fetch_known_categories(conn, stock_id)

        if not applicant_aliases:
            raise PatentApiError("At least one stock name or applicant name is required.")

        collected: list[KiprisPatentRecord] = []
        seen_application_nos: set[str] = set()
        try:
            for applicant in applicant_aliases:
                page_no = 1
                received_for_alias = 0
                while True:
                    records, total = await self._client.search_by_applicant(
                        applicant=applicant,
                        start_date=start,
                        end_date=end,
                        page_no=page_no,
                    )
                    received_for_alias += len(records)
                    for record in records:
                        if record.application_no in seen_application_nos:
                            continue
                        seen_application_nos.add(record.application_no)
                        collected.append(record)
                    if not records or received_for_alias >= total:
                        break
                    page_no += 1
        except Exception as exc:
            async with self._pool.acquire() as conn:
                await _finish_run(conn, run_id=run_id, status="failed", error_message=str(exc))
            raise

        counts = await self._persist_records(
            stock_id=stock_id,
            run_id=run_id,
            collected=collected,
            known_categories=known_categories,
        )
        inserted = counts["inserted"]
        skipped = counts["skipped"]
        requeued = counts["requeued"]
        updated = counts["updated"]
        failed = counts["failed"]

        # requeued(F1 재인큐 복구)·updated(KIPRIS 우선 승격)는 usable·non-failed 결과다.
        # collector_runs 에는 두 컬럼이 없어 DB 의 skipped_count 에 합산하고, 반환 dict
        # 에만 별도로 노출한다.
        status = _run_status(inserted, skipped + requeued + updated, failed)
        async with self._pool.acquire() as conn:
            await _finish_run(
                conn,
                run_id=run_id,
                status=status,
                collected_count=len(collected),
                inserted_count=inserted,
                skipped_count=skipped + requeued + updated,
                failed_count=failed,
            )
        return {
            "collector_run_id": run_id,
            "collected_count": len(collected),
            "inserted_count": inserted,
            "skipped_count": skipped,
            "requeued_count": requeued,
            "updated_count": updated,
            "failed_count": failed,
            "status": status,
        }

    async def ingest_records(
        self,
        *,
        stock_id: int,
        records: list[KiprisPatentRecord],
        source_name: str = SOURCE_NAME,
    ) -> dict[str, Any]:
        """Persist pre-collected patent records (e.g. a Google Patents BigQuery
        backfill) through the exact same DB contract as :meth:`run`.

        Unlike :meth:`run`, the caller supplies the records (no KIPRIS API call),
        so this is the seam for alternative patent sources. It opens a
        ``collector_runs`` row, de-duplicates by ``application_no``, persists each
        record via :meth:`_save_record` (raw + detail + queue, F1 safety net), and
        finalizes the run status. Re-running is idempotent: already-collected
        patents skip on the ``source_hash`` UNIQUE constraint.
        """
        seen: set[str] = set()
        collected: list[KiprisPatentRecord] = []
        for record in records:
            key = canonicalize_application_no(record.application_no)
            if key and key not in seen:
                seen.add(key)
                collected.append(record)

        async with self._pool.acquire() as conn:
            run_id = await _create_run(conn)
            known_categories = await _fetch_known_categories(conn, stock_id)

        counts = await self._persist_records(
            stock_id=stock_id,
            run_id=run_id,
            collected=collected,
            known_categories=known_categories,
            source_name=source_name,
        )
        non_new = counts["skipped"] + counts["requeued"] + counts["updated"]
        status = _run_status(counts["inserted"], non_new, counts["failed"])
        async with self._pool.acquire() as conn:
            await _finish_run(
                conn,
                run_id=run_id,
                status=status,
                collected_count=len(collected),
                inserted_count=counts["inserted"],
                skipped_count=non_new,
                failed_count=counts["failed"],
            )
        return {
            "collector_run_id": run_id,
            "collected_count": len(collected),
            "inserted_count": counts["inserted"],
            "skipped_count": counts["skipped"],
            "requeued_count": counts["requeued"],
            "updated_count": counts["updated"],
            "failed_count": counts["failed"],
            "status": status,
        }

    async def _persist_records(
        self,
        *,
        stock_id: int,
        run_id: int,
        collected: list[KiprisPatentRecord],
        known_categories: set[str],
        source_name: str = SOURCE_NAME,
    ) -> dict[str, int]:
        """Save each record and tally outcomes. Shared by :meth:`run` (KIPRIS) and
        :meth:`ingest_records` (external backfills) so the persistence contract
        lives in one place."""
        inserted = skipped = requeued = updated = failed = 0
        for record in collected:
            try:
                saved = await self._save_record(
                    stock_id=stock_id,
                    run_id=run_id,
                    record=record,
                    known_categories=known_categories,
                    source_name=source_name,
                )
                if saved == "inserted":
                    inserted += 1
                    tech_cat = _tech_category(record.ipc_code)
                    if tech_cat:
                        known_categories.add(tech_cat)
                elif saved == "requeued":
                    requeued += 1
                elif saved == "updated":
                    # KIPRIS 우선 승격(기존 저순위 소스 행을 이번 레코드로 덮어씀).
                    updated += 1
                else:
                    skipped += 1
            except Exception:
                failed += 1
        return {
            "inserted": inserted,
            "skipped": skipped,
            "requeued": requeued,
            "updated": updated,
            "failed": failed,
        }

    async def _save_record(
        self,
        *,
        stock_id: int,
        run_id: int,
        record: KiprisPatentRecord,
        known_categories: set[str],
        source_name: str = SOURCE_NAME,
    ) -> str:
        """Insert raw + detail + queue in one transaction.

        Returns the per-record outcome: ``"inserted"`` (new raw), ``"skipped"``
        (duplicate with an active/successful NORMALIZE task already present), or
        ``"requeued"`` (F1: duplicate raw whose normalization was never completed —
        a fresh NORMALIZE task is re-enqueued so it can self-recover).

        ``source_name`` distinguishes the origin in ``raw_documents`` (default
        ``KIPRIS``; backfills from other registries, e.g. Google Patents via
        BigQuery, pass their own). ``source_hash`` is
        ``PATENT|canonicalize_application_no(application_no)`` — the canonical key
        collapses the *same* patent across sources (KIPRIS' 13-digit form and
        BigQuery's ``KR-...-A`` form reduce to the same key), so an overlapping
        patent de-duplicates on the UNIQUE constraint regardless of which source
        delivered it. ``external_id`` keeps the source-native string for traceback.
        """
        source_hash = make_source_hash(SOURCE_TYPE, canonicalize_application_no(record.application_no))
        external_id = record.application_no
        application_date = _parse_date(record.application_date, application_no=record.application_no)
        # 공개일(출원 후 ~18개월). KIPRIS=OpeningDate, BigQuery=publication_date 가
        # record.open_date 로 실려 온다. 미상이면 NULL(분석기가 application_date 로 폴백).
        publication_date = _parse_optional_date(record.open_date)
        tech_cat = _tech_category(record.ipc_code)
        is_new_category = (tech_cat is not None) and (tech_cat not in known_categories)

        async with self._pool.acquire() as conn:
            try:
                async with conn.transaction():
                    raw_id = await conn.fetchval(
                        """
                        INSERT INTO raw_documents (
                            stock_id, collector_run_id, source_type, source_name,
                            external_id, source_hash, title, published_at,
                            collect_status, collector_ver
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        RETURNING id
                        """,
                        stock_id,
                        run_id,
                        SOURCE_TYPE,
                        source_name,
                        external_id,
                        source_hash,
                        record.invention_title or external_id,
                        application_date,
                        "success",
                        self._collector_ver,
                    )
                    await conn.execute(
                        """
                        INSERT INTO patent_raw_details (
                            raw_document_id, stock_id, application_no, patent_title,
                            applicant_name, application_date, publication_date,
                            tech_category, is_new_category, extra_payload
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                        """,
                        raw_id,
                        stock_id,
                        record.application_no,
                        record.invention_title or record.application_no,
                        record.applicant_name,
                        application_date,
                        publication_date,
                        tech_cat,
                        is_new_category,
                        json.dumps(record.raw, ensure_ascii=False),
                    )
                    await conn.execute(
                        """
                        INSERT INTO processing_queue (
                            stock_id, task_type, status, priority,
                            source_raw_ids, task_context
                        )
                        VALUES ($1, $2, 'pending', 'batch', $3, $4)
                        """,
                        stock_id,
                        TASK_TYPE,
                        [raw_id],
                        json.dumps(
                            {
                                "collector_run_id": run_id,
                                "source_type": SOURCE_TYPE,
                                "collector_ver": self._collector_ver,
                            },
                            ensure_ascii=False,
                        ),
                    )
                return "inserted"
            except asyncpg.exceptions.UniqueViolationError:
                pass
        # 중복(이미 수집된 같은 canonical 특허). KIPRIS 우선 정책: 기존 행이 우선순위
        # 낮은 소스(GOOGLE_PATENTS)면 이번(더 높은 우선순위) 레코드로 승격(덮어쓰기)한다.
        promoted = await self._promote_source_if_outranked(
            source_hash=source_hash,
            incoming_source=source_name,
            record=record,
            application_date=application_date,
            publication_date=publication_date,
            tech_category=tech_cat,
            is_new_category=is_new_category,
        )
        if promoted:
            return "updated"
        # 승격 대상 아님(동일·상위 소스가 이미 있음) → 정규화 task 가 활성/성공이면
        # 진짜 skip, 하나도 없으면(실패·유실·미생성) F1 안전망으로 재인큐한다.
        requeued = await self._requeue_if_unprocessed(
            stock_id=stock_id, run_id=run_id, source_hash=source_hash
        )
        return "requeued" if requeued else "skipped"

    async def _promote_source_if_outranked(
        self,
        *,
        source_hash: str,
        incoming_source: str,
        record: KiprisPatentRecord,
        application_date: date,
        publication_date: date | None,
        tech_category: str | None,
        is_new_category: bool,
    ) -> bool:
        """기존 raw 행이 이번 소스보다 우선순위가 낮으면 이번 레코드로 덮어쓴다.

        cross-source dedup 은 ``source_hash`` UNIQUE 로 canonical 특허당 1행만 남긴다
        (first-writer-wins). 사용자 정책 = **KIPRIS 우선**: BigQuery(GOOGLE_PATENTS)가
        먼저 적재한 행을 나중에 온 KIPRIS 가 덮어써 authoritative 소스로 승격한다.
        raw_documents.source_name + 식별/일시 필드와 patent_raw_details 상세를 함께
        갱신한다(공개일 포함). 우선순위가 같거나 낮으면(예: 이미 KIPRIS) 아무것도 안 함.

        Returns True 면 승격 발생(호출부가 "updated" 로 집계).
        """
        incoming_rank = _SOURCE_PRIORITY.get(incoming_source, 0)
        async with self._pool.acquire() as conn:
            existing = await conn.fetchrow(
                "SELECT id, source_name FROM raw_documents WHERE source_hash = $1",
                source_hash,
            )
            if existing is None:
                return False
            existing_rank = _SOURCE_PRIORITY.get(existing["source_name"], 0)
            if incoming_rank <= existing_rank:
                return False  # 동일·상위 소스가 이미 있음 → 덮어쓰지 않음
            raw_id = existing["id"]
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE raw_documents
                    SET source_name = $2, external_id = $3, title = $4, published_at = $5
                    WHERE id = $1
                    """,
                    raw_id,
                    incoming_source,
                    record.application_no,
                    record.invention_title or record.application_no,
                    application_date,
                )
                await conn.execute(
                    """
                    UPDATE patent_raw_details
                    SET patent_title = $2, applicant_name = $3, application_date = $4,
                        publication_date = $5, tech_category = $6, is_new_category = $7,
                        extra_payload = $8
                    WHERE raw_document_id = $1
                    """,
                    raw_id,
                    record.invention_title or record.application_no,
                    record.applicant_name,
                    application_date,
                    publication_date,
                    tech_category,
                    is_new_category,
                    json.dumps(record.raw, ensure_ascii=False),
                )
        return True

    async def _requeue_if_unprocessed(
        self,
        *,
        stock_id: int,
        run_id: int,
        source_hash: str,
    ) -> bool:
        """F1: re-enqueue NORMALIZE for an existing raw that has no live/done task.

        Returns True if a new task was enqueued. UniqueViolation 으로 롤백된 뒤라
        새 트랜잭션에서 기존 raw_id 를 source_hash(UNIQUE)로 찾는다.
        """
        async with self._pool.acquire() as conn:
            raw_id = await conn.fetchval(
                "SELECT id FROM raw_documents WHERE source_hash = $1",
                source_hash,
            )
            if raw_id is None:
                return False
            queue = ProcessingQueueRepository(conn)
            if await queue.has_open_or_successful_task(
                raw_document_id=raw_id, task_type=TASK_TYPE
            ):
                return False
            async with conn.transaction():
                await conn.execute(
                    """
                    INSERT INTO processing_queue (
                        stock_id, task_type, status, priority,
                        source_raw_ids, task_context
                    )
                    VALUES ($1, $2, 'pending', 'batch', $3, $4)
                    """,
                    stock_id,
                    TASK_TYPE,
                    [raw_id],
                    json.dumps(
                        {
                            "collector_run_id": run_id,
                            "source_type": SOURCE_TYPE,
                            "collector_ver": self._collector_ver,
                            "requeue_reason": "f1_normalization_safety_net",
                        },
                        ensure_ascii=False,
                    ),
                )
            return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_IPC_SUBCLASS_MAP: dict[str, str] = {
    "G06": "AI/Software",
    "G16": "Information Technology",
    "H04": "Communications",
    "H01L": "Semiconductors",
    "H02": "Power Engineering",
    "A61": "Pharmaceuticals",
    "C12": "Biotechnology",
    "B60": "Automotive",
    "G01": "Measurement/Sensors",
}

_IPC_SECTION_MAP: dict[str, str] = {
    "A": "Life Sciences",
    "B": "Industrial Processes",
    "C": "Chemistry",
    "D": "Textiles",
    "E": "Construction",
    "F": "Mechanical Engineering",
    "G": "Physics/IT",
    "H": "Electronics/Semiconductors",
}


def _tech_category(ipc_code: str | None) -> str | None:
    if not ipc_code:
        return None
    code = ipc_code.strip().upper()
    for prefix, category in _IPC_SUBCLASS_MAP.items():
        if code.startswith(prefix):
            return category
    first = code[0] if code else ""
    return _IPC_SECTION_MAP.get(first)


def _parse_date(value: str | None, *, application_no: str | None = None) -> date:
    if not value:
        logger.warning(
            "Patent %s has no application_date; falling back to today().",
            application_no or "<unknown>",
        )
        return date.today()
    value = value.strip()
    if len(value) == 8 and value.isdigit():
        return datetime.strptime(value, "%Y%m%d").date()
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        logger.warning(
            "Patent %s has unparseable application_date %r; falling back to today().",
            application_no or "<unknown>",
            value,
        )
        return date.today()


def _parse_optional_date(value: str | None) -> date | None:
    """Parse an optional YYYYMMDD/ISO date, returning None when absent/unparseable.

    Used for publication_date(공개일): unlike application_date it must NOT fall back
    to today() — a missing/garbled 공개일 is stored as NULL so the analyzer can
    COALESCE to application_date instead of inventing a fake publication date.
    """
    if not value:
        return None
    value = value.strip()
    if not value:
        return None
    if len(value) == 8 and value.isdigit():
        try:
            return datetime.strptime(value, "%Y%m%d").date()
        except ValueError:
            return None
    try:
        return datetime.fromisoformat(value).date()
    except ValueError:
        return None


async def _create_run(conn: Any) -> int:
    return await conn.fetchval(
        """
        INSERT INTO collector_runs (collector_type, run_mode, status)
        VALUES ($1, 'batch', 'running')
        RETURNING id
        """,
        SOURCE_TYPE,
    )


async def _fetch_known_categories(conn: Any, stock_id: int) -> set[str]:
    rows = await conn.fetch(
        """
        SELECT DISTINCT tech_category
        FROM patent_raw_details
        WHERE stock_id = $1 AND tech_category IS NOT NULL
        """,
        stock_id,
    )
    return {row["tech_category"] for row in rows}


async def _fetch_stock_alias_seeds(conn: Any, *, stock_id: int, stock_code: str) -> dict[str, Any]:
    row = await conn.fetchrow(
        """
        SELECT
            s.name AS stock_name,
            d.corp_name,
            d.corp_name_eng,
            d.stock_name AS dart_stock_name
        FROM stocks s
        LEFT JOIN dart_corp_codes d ON d.stock_id = s.id AND d.is_active = TRUE
        WHERE s.id = $1
           OR s.ticker = $2
        ORDER BY CASE WHEN s.id = $1 THEN 0 ELSE 1 END
        LIMIT 1
        """,
        stock_id,
        stock_code,
    )
    if row is None:
        return {"stock_name": None, "applicant_names": []}

    stock_name = row["stock_name"]
    applicant_names = [
        value
        for value in (row["corp_name"], row["corp_name_eng"], row["dart_stock_name"])
        if value and value != stock_name
    ]
    return {"stock_name": stock_name, "applicant_names": applicant_names}


async def _finish_run(
    conn: Any,
    *,
    run_id: int,
    status: str,
    collected_count: int = 0,
    inserted_count: int = 0,
    skipped_count: int = 0,
    failed_count: int = 0,
    error_message: str | None = None,
) -> None:
    await conn.execute(
        """
        UPDATE collector_runs
        SET
            status = $2,
            finished_at = NOW(),
            collected_count = $3,
            inserted_count = $4,
            skipped_count = $5,
            failed_count = $6,
            error_message = $7
        WHERE id = $1
        """,
        run_id,
        status,
        collected_count,
        inserted_count,
        skipped_count,
        failed_count,
        error_message,
    )
