from __future__ import annotations

from typing import Any

from signal_alpha_data_access.repositories import ProcessingQueueRepository

LEGACY_DART_BACKFILL_TASK_TYPE = "backfill_dart_labels"
LEGACY_QUEUE_ACTIVE_STATUSES = ("pending", "retrying", "running")
LEGACY_DART_BACKFILL_SKIP_MESSAGE = (
    "legacy DART ML label backfill task removed; skipped during queue cleanup"
)


async def cleanup_legacy_dart_backfill_tasks(
    connection: Any,
    *,
    execute: bool = False,
    limit: int = 1000,
) -> dict[str, Any]:
    repository = ProcessingQueueRepository(connection)
    by_status = await repository.count_tasks_by_status(
        task_type=LEGACY_DART_BACKFILL_TASK_TYPE,
        statuses=LEGACY_QUEUE_ACTIVE_STATUSES,
    )
    matched_count = sum(by_status.values())
    updated_count = 0

    if execute and matched_count:
        updated_count = await repository.mark_tasks_skipped_by_type(
            task_type=LEGACY_DART_BACKFILL_TASK_TYPE,
            statuses=LEGACY_QUEUE_ACTIVE_STATUSES,
            message=LEGACY_DART_BACKFILL_SKIP_MESSAGE,
            limit=limit,
        )

    return {
        "task_type": LEGACY_DART_BACKFILL_TASK_TYPE,
        "statuses": list(LEGACY_QUEUE_ACTIVE_STATUSES),
        "dry_run": not execute,
        "matched_count": matched_count,
        "updated_count": updated_count,
        "by_status": by_status,
    }
