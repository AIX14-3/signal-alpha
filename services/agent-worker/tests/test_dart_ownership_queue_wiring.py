from app.orchestrator.queue.drain_daemon import DRAIN_ORDER
from app.orchestrator.queue.task_types import (
    COLLECT_DART,
    COLLECT_DART_OWNERSHIP,
    NORMALIZE_DART,
)
from app.orchestrator.queue.tasks import DEFAULT_CYCLE_PLAN


def test_collect_dart_ownership_is_drained_between_dart_collect_and_normalize():
    assert COLLECT_DART_OWNERSHIP == "collect_dart_ownership"
    assert DEFAULT_CYCLE_PLAN[COLLECT_DART_OWNERSHIP] == 2
    assert DRAIN_ORDER.index(COLLECT_DART) < DRAIN_ORDER.index(COLLECT_DART_OWNERSHIP)
    assert DRAIN_ORDER.index(COLLECT_DART_OWNERSHIP) < DRAIN_ORDER.index(NORMALIZE_DART)
