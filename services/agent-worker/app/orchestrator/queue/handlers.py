from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.orchestrator.queue.task_types import ANALYZE_DART, COLLECT_DART, NORMALIZE_DART
from app.orchestrator.queue.tasks import TaskHandler


def build_task_handlers(connection: Any) -> dict[str, TaskHandler]:
    from app.orchestrator.dart.tasks import (
        DartAnalyzeTaskHandler,
        DartCollectionTaskHandler,
        DartNormalizeTaskHandler,
    )

    settings = get_settings()
    return {
        COLLECT_DART: DartCollectionTaskHandler(
            connection=connection,
            settings=settings,
        ),
        NORMALIZE_DART: DartNormalizeTaskHandler(connection),
        ANALYZE_DART: DartAnalyzeTaskHandler(connection),
    }
