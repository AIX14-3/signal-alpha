"""Per-stock multi-source pipeline trigger.

The worker has no auto-consumer loop and historically only the DART chain was
wired end-to-end, so a report showed DART and left the other sources ``missing``.
This module enqueues every report source for one stock in a single call so the
operator (or a scheduler) can fan a stock out across all collectors:

  - ANALYZE_PRICE          → PRICE analysis_result (ohlcv_data)
  - ANALYZE_ALTERNATIVE    → HIRING/PATENT/DATALAB analysis_results
  - COLLECT_DART (chain)   → DART analysis_result → ML → fan-in AGGREGATE
  - AGGREGATE_SIGNAL       → fan-in blend of whatever sources exist for the date

The DART chain already enqueues a fan-in AGGREGATE after ML/META; the extra
AGGREGATE here guarantees an AGGREGATED row exists even before the DART chain is
drained (re-draining after more sources finish simply refreshes the blend — the
aggregate handler upserts by (stock, date, run_key)).
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.orchestrator.queue.task_types import (
    AGGREGATE_SIGNAL,
    ANALYZE_ALTERNATIVE,
    ANALYZE_PRICE,
)


async def enqueue_stock_pipeline(
    queue: Any,
    *,
    stock_id: int,
    stock_code: str,
    signal_date: date,
    priority: str = "batch",
    include_dart_collection: bool = False,
    collect_dart_task_type: str = "collect_dart",
) -> dict[str, int | None]:
    """Enqueue the full per-stock source fan-out. Returns the enqueued task ids.

    ``include_dart_collection`` is opt-in because DART collection carries its own
    date-range/scheduler semantics (``DartCollectionScheduler``); when False the
    caller is expected to drive DART through its existing scheduler. PRICE and the
    ALTERNATIVE trio have no such windowing, so they are always enqueued here.
    """
    as_of = signal_date.isoformat()
    base_ctx = {"stock_code": stock_code, "signal_date": as_of, "as_of": as_of}

    price_task_id = await queue.enqueue(
        stock_id=stock_id,
        task_type=ANALYZE_PRICE,
        priority=priority,
        task_context=base_ctx,
        dedupe=True,
    )
    alternative_task_id = await queue.enqueue(
        stock_id=stock_id,
        task_type=ANALYZE_ALTERNATIVE,
        priority=priority,
        task_context=base_ctx,
        dedupe=True,
    )
    dart_task_id: int | None = None
    if include_dart_collection:
        dart_task_id = await queue.enqueue(
            stock_id=stock_id,
            task_type=collect_dart_task_type,
            priority=priority,
            task_context=base_ctx,
            dedupe=True,
        )
    # Fan-in aggregate: no source_analysis_result_ids → the handler gathers every
    # source's latest result for (stock_id, signal_date).
    aggregate_task_id = await queue.enqueue(
        stock_id=stock_id,
        task_type=AGGREGATE_SIGNAL,
        priority=priority,
        task_context={"stock_code": stock_code, "signal_date": as_of, "run_key": "AGGREGATED"},
        dedupe=True,
    )
    return {
        "analyze_price_task_id": price_task_id,
        "analyze_alternative_task_id": alternative_task_id,
        "collect_dart_task_id": dart_task_id,
        "aggregate_signal_task_id": aggregate_task_id,
    }
