"""REQUERY_SOURCE handler — the orchestrator's conditional bounded re-ask.

When ``AggregateSignalTaskHandler`` detects disagreement it enqueues ONE
``REQUERY_SOURCE`` task carrying the problem source(s) and a focus hint. This
handler re-interprets *already-collected* raw data for exactly those sources
(re-analysis, not re-collection — plan §2.2 "되묻기는 분석 계층에만"), then
re-enqueues ``AGGREGATE_SIGNAL`` so the fan-in re-blends with the refreshed
per-source result.

Invariants / safety:
  - **Bounded.** ``task_context.requery_round`` is incremented each hop and the
    re-enqueued AGGREGATE carries it; the aggregator refuses to spawn another
    re-query past ``MAX_REQUERY_ROUNDS`` — **this round cap alone bounds the
    assemble→detect→requery→assemble loop** (detect may stay True every round).
    Queue dedupe does NOT help here: each re-enqueued AGGREGATE carries a distinct
    ``requery_round`` in its task_context, so hops are distinct rows; dedupe only
    collapses duplicate *same-round* enqueues.
  - **Structural skip.** Only re-queryable sources with a live alternative
    registration are driven; DART/PRICE/REPORT (team-owned, no seam yet) are
    skipped, not errored — so the loop degrades gracefully before Wave-2 source
    agents land.
  - **Deterministic fallback.** Any per-source re-analysis failure is isolated
    (the underlying handler already degrades to ``failed`` / RuleSourceAgent); the
    AGGREGATE re-enqueue still fires so publishing is never blocked.
  - **Numbers untouched.** Re-query only refreshes source *evidence/features*; the
    headline is still the meta-learner's SRC channel at aggregate time.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from app.orchestrator.aggregation.detect import REQUERYABLE_SOURCES
from app.orchestrator.queue.task_types import AGGREGATE_SIGNAL
from signal_alpha_data_access.repositories import ProcessingQueueRepository

logger = logging.getLogger(__name__)

# Hard cap on assemble→detect→requery→assemble hops for one (stock, date).
MAX_REQUERY_ROUNDS = 2


class RequerySourceTaskHandler:
    """Drive a focused re-analysis of the flagged source(s), then re-aggregate.

    ``analyze_handler_factory`` builds an ``AlternativeAnalyzeTaskHandler`` bound
    to a single-source registration — injected so tests can stub the (heavy)
    analyze path. Default lazily constructs the real one from the connection.
    """

    def __init__(
        self,
        connection: Any,
        *,
        analyze_handler_factory: Any | None = None,
    ) -> None:
        self._connection = connection
        self._queue_repository = ProcessingQueueRepository(connection)
        self._analyze_handler_factory = analyze_handler_factory or _default_analyze_handler_factory

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        task_context = _task_context(task.get("task_context"))
        requested = _target_sources(task_context.get("target_sources"))
        requery_round = int(task_context.get("requery_round") or 1)
        focus = task_context.get("focus") or task_context.get("disagreement_reasons")

        requeried: list[str] = []
        skipped: list[str] = []
        for source in requested:
            if source not in REQUERYABLE_SOURCES:
                skipped.append(source)
                continue
            handler = self._build_analyze_handler(source)
            if handler is None:
                skipped.append(source)
                continue
            try:
                await handler(
                    {
                        "stock_id": stock_id,
                        "task_context": {
                            **{k: v for k, v in task_context.items() if k in ("as_of", "stock_code")},
                            # Focused question surfaced to the source agent's context
                            # (agents that read it can narrow their re-read; those
                            # that don't are byte-identical to a normal analyze).
                            "requery_focus": focus,
                            "requery_round": requery_round,
                        },
                    }
                )
                requeried.append(source)
            except Exception as exc:  # noqa: BLE001 — isolate; re-aggregate anyway
                logger.warning(
                    "REQUERY_SOURCE 재분석 실패 (stock=%s, source=%s): %s — 해당 소스 건너뛰고 재종합",
                    stock_id,
                    source,
                    exc,
                )
                skipped.append(source)

        # Always re-aggregate so the (possibly refreshed) fan-in re-publishes.
        # requery_round is carried forward so the aggregator's bound applies.
        aggregate_task_id = await self._queue_repository.enqueue(
            stock_id=stock_id,
            task_type=AGGREGATE_SIGNAL,
            priority=str(task_context.get("priority") or "batch"),
            task_context={
                **{k: v for k, v in task_context.items() if k in ("as_of", "stock_code", "signal_date", "priority")},
                "requery_round": requery_round,
            },
            dedupe=True,
        )
        return {
            "requeried_sources": requeried,
            "skipped_sources": skipped,
            "requery_round": requery_round,
            "aggregate_task_id": aggregate_task_id,
        }

    def _build_analyze_handler(self, source: str) -> Any | None:
        try:
            return self._analyze_handler_factory(self._connection, source)
        except Exception as exc:  # noqa: BLE001 — no registration/agent for source: skip
            logger.info(
                "REQUERY_SOURCE: %s 분석기 미구성 (%s) — 구조적 skip", source, exc
            )
            return None


def _default_analyze_handler_factory(connection: Any, source: str) -> Any:
    from app.analyzers.registry import registration_for
    from app.orchestrator.alternative.tasks import AlternativeAnalyzeTaskHandler

    return AlternativeAnalyzeTaskHandler(
        connection, registrations=[registration_for(source)]
    )


def _target_sources(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [value.strip().upper()] if value.strip() else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip().upper() for item in value if str(item).strip()]
    return []


def _task_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        import json

        return json.loads(value)
    return dict(value)
