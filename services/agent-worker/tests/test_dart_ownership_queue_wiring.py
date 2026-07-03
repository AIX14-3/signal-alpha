from app.orchestrator.queue.drain_daemon import DRAIN_ORDER
from app.orchestrator.queue.task_types import (
    ANALYZE_DART,
    BACKFILL_DART_LABELS,
    COLLECT_DART,
    COLLECT_DART_OWNERSHIP,
    NORMALIZE_DART,
    NORMALIZE_DART_OWNERSHIP,
)
from app.orchestrator.queue.tasks import DEFAULT_CYCLE_PLAN


def test_collect_dart_ownership_is_drained_between_dart_collect_and_normalize():
    assert COLLECT_DART_OWNERSHIP == "collect_dart_ownership"
    assert NORMALIZE_DART_OWNERSHIP == "normalize_dart_ownership"
    assert BACKFILL_DART_LABELS == "backfill_dart_labels"
    assert DEFAULT_CYCLE_PLAN[COLLECT_DART_OWNERSHIP] == 2
    assert DEFAULT_CYCLE_PLAN[NORMALIZE_DART_OWNERSHIP] == 10
    assert DEFAULT_CYCLE_PLAN[BACKFILL_DART_LABELS] == 10
    assert DRAIN_ORDER.index(COLLECT_DART) < DRAIN_ORDER.index(COLLECT_DART_OWNERSHIP)
    assert DRAIN_ORDER.index(COLLECT_DART_OWNERSHIP) < DRAIN_ORDER.index(NORMALIZE_DART_OWNERSHIP)
    assert DRAIN_ORDER.index(NORMALIZE_DART_OWNERSHIP) < DRAIN_ORDER.index(NORMALIZE_DART)
    assert DRAIN_ORDER.index(NORMALIZE_DART) < DRAIN_ORDER.index(BACKFILL_DART_LABELS)
    assert DRAIN_ORDER.index(BACKFILL_DART_LABELS) < DRAIN_ORDER.index(ANALYZE_DART)
