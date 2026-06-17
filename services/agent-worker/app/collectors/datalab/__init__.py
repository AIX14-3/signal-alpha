"""DataLab collector — source_type='DATALAB', source_name='NAVER_DATALAB'.

Collects by category (search theme), not by stock.
The Normalizer resolves category → stock via datalab_category_stocks.

DB flow per record:
  datalab_raw_documents → datalab_raw_details → processing_queue(stock_id=NULL)
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from typing import Any

import asyncpg  # type: ignore[import]
import asyncpg.exceptions  # type: ignore[import]

from app.clients.naver_datalab_client import NaverDataLabClient, NaverDataLabRecord, _default_end_date, _default_start_date
from app.observability import calculate_run_status as _run_status
from app.utils.hash_utils import make_source_hash

SOURCE_TYPE = "DATALAB"
SOURCE_NAME = "NAVER_DATALAB"
TASK_TYPE = "NORMALIZE_DATALAB"

SEARCH_INDEX_SPIKE_THRESHOLD = 80.0
CHANGE_PCT_SPIKE_THRESHOLD = 50.0


class DataLabCollector:
    """Collects search trend data from Naver DataLab and stores per DB contract."""

    def __init__(
        self,
        *,
        pool: Any,
        client: NaverDataLabClient,
        collector_ver: str | None = None,
    ) -> None:
        self._pool = pool
        self._client = client
        self._collector_ver = collector_ver or os.getenv("COLLECTOR_VERSION", "1.0")

    async def run(
        self,
        *,
        category_id: int,
        category_name: str,
        keyword_group: str,
        keywords: list[str],
        start_date: str | None = None,
        end_date: str | None = None,
        time_unit: str = "date",
        device: str = "all",
        gender: str = "all",
        age_group: str = "all",
    ) -> dict[str, Any]:
        start = start_date or _default_start_date()
        end = end_date or _default_end_date()
        period_type = NaverDataLabClient.time_unit_to_period_type(time_unit)
        # Normalize to "all" — DB contract forbids NULL for these columns
        device_stored = device or "all"
        gender_stored = gender or "all"
        age_group_stored = age_group or "all"

        async with self._pool.acquire() as conn:
            run_id = await _create_run(conn)

        # Map stored "all" back to empty string for Naver API filter params
        api_device = "" if device_stored == "all" else device_stored
        api_gender = "" if gender_stored == "all" else gender_stored
        api_ages = _age_group_to_ages(age_group_stored)

        try:
            records = await self._client.search(
                keyword_group=keyword_group,
                keywords=keywords,
                start_date=start,
                end_date=end,
                time_unit=time_unit,
                device=api_device,
                gender=api_gender,
                ages=api_ages,
            )
        except Exception as exc:
            async with self._pool.acquire() as conn:
                await _finish_run(conn, run_id=run_id, status="failed", error_message=str(exc))
            raise

        # Track previous search index per keyword to compute change_pct
        keyword_previous: dict[str, float] = {}
        inserted = skipped = failed = 0

        for record in records:
            prev = keyword_previous.get(record.keyword)
            try:
                saved = await self._save_record(
                    category_id=category_id,
                    category_name=category_name,
                    run_id=run_id,
                    record=record,
                    period_type=period_type,
                    previous_search_index=prev,
                    device=device_stored,
                    gender=gender_stored,
                    age_group=age_group_stored,
                )
                if saved:
                    inserted += 1
                else:
                    skipped += 1
                keyword_previous[record.keyword] = record.ratio
            except Exception:
                failed += 1
                keyword_previous[record.keyword] = record.ratio

        status = _run_status(inserted, skipped, failed)
        async with self._pool.acquire() as conn:
            await _finish_run(
                conn,
                run_id=run_id,
                status=status,
                collected_count=len(records),
                inserted_count=inserted,
                skipped_count=skipped,
                failed_count=failed,
            )
        return {
            "collector_run_id": run_id,
            "collected_count": len(records),
            "inserted_count": inserted,
            "skipped_count": skipped,
            "failed_count": failed,
            "status": status,
        }

    async def _save_record(
        self,
        *,
        category_id: int,
        category_name: str,
        run_id: int,
        record: NaverDataLabRecord,
        period_type: str,
        previous_search_index: float | None,
        device: str = "all",
        gender: str = "all",
        age_group: str = "all",
    ) -> bool:
        """Insert raw + detail + queue in one transaction. Returns True if inserted, False if skipped."""
        observed_date = record.period
        # asyncpg needs a date object for DATE/TIMESTAMPTZ params; the string
        # form is kept for source_hash and external_id.
        observed_date_obj = date.fromisoformat(observed_date)
        source_hash = make_source_hash(
            SOURCE_TYPE,
            category_id,
            record.keyword,
            observed_date,
            period_type,
            device,
            gender,
            age_group,
        )
        external_id = (
            f"{category_id}_{record.keyword}_{observed_date}"
            f"_{period_type}_{device}_{gender}_{age_group}"
        )
        change_pct = _compute_change_pct(record.ratio, previous_search_index)
        is_spike = (
            record.ratio >= SEARCH_INDEX_SPIKE_THRESHOLD
            or (change_pct is not None and change_pct >= CHANGE_PCT_SPIKE_THRESHOLD)
        )
        title = f"[DataLab] {category_name} / {record.keyword} {observed_date}"

        async with self._pool.acquire() as conn:
            try:
                async with conn.transaction():
                    raw_id = await conn.fetchval(
                        """
                        INSERT INTO datalab_raw_documents (
                            category_id, collector_run_id, source_name,
                            external_id, source_hash, title, published_at,
                            collect_status, collector_ver
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        RETURNING id
                        """,
                        category_id,
                        run_id,
                        SOURCE_NAME,
                        external_id,
                        source_hash,
                        title,
                        observed_date_obj,
                        "success",
                        self._collector_ver,
                    )
                    await conn.execute(
                        """
                        INSERT INTO datalab_raw_details (
                            raw_document_id, category_id, keyword, keyword_group,
                            observed_date, search_index, previous_search_index,
                            change_pct, period_type, device, gender, age_group,
                            is_spike, extra_payload
                        )
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                        """,
                        raw_id,
                        category_id,
                        record.keyword,
                        record.keyword_group,
                        observed_date_obj,
                        record.ratio,
                        previous_search_index,
                        change_pct,
                        period_type,
                        device,
                        gender,
                        age_group,
                        is_spike,
                        json.dumps(record.raw_data, ensure_ascii=False),
                    )
                    await conn.execute(
                        """
                        INSERT INTO processing_queue (
                            stock_id, task_type, status, priority,
                            source_raw_ids, task_context
                        )
                        VALUES ($1, $2, 'pending', 'batch', $3, $4)
                        """,
                        None,  # DataLab tasks have no stock_id; Normalizer resolves via datalab_category_stocks
                        TASK_TYPE,
                        [raw_id],
                        json.dumps(
                            {
                                "collector_run_id": run_id,
                                "source_type": SOURCE_TYPE,
                                "category_id": category_id,
                                "keyword": record.keyword,
                                "period_type": period_type,
                                "collector_ver": self._collector_ver,
                            },
                            ensure_ascii=False,
                        ),
                    )
                return True
            except asyncpg.exceptions.UniqueViolationError:
                return False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _age_group_to_ages(age_group: str) -> list[str]:
    """Map the stored age_group to Naver DataLab API age codes.

    'all' (the default) means no age filter → empty list, matching the API's
    "aggregate across all ages" behaviour. A non-'all' value may carry one or
    more Naver age codes ("1".."11") separated by comma/plus, which are passed
    through so the filter actually reaches the API instead of being dropped.
    """
    if not age_group or age_group == "all":
        return []
    return [code.strip() for code in re.split(r"[,+]", age_group) if code.strip()]


def _compute_change_pct(current: float, previous: float | None) -> float | None:
    if previous is None:
        return None
    if previous == 0:
        return 999.99 if current > 0 else 0.0
    return round((current - previous) / previous * 100, 2)


async def _create_run(conn: Any) -> int:
    return await conn.fetchval(
        """
        INSERT INTO collector_runs (collector_type, run_mode, status)
        VALUES ($1, 'batch', 'running')
        RETURNING id
        """,
        SOURCE_TYPE,
    )


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
