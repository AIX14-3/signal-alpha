from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.orchestrator.task_types import COLLECT_DART, NORMALIZE_DART
from app.orchestrator.tasks import TaskHandler


def build_task_handlers(connection: Any) -> dict[str, TaskHandler]:
    from app.orchestrator.dart_tasks import DartCollectionTaskHandler, DartNormalizeTaskHandler

    settings = get_settings()
    return {
        COLLECT_DART: DartCollectionTaskHandler(
            connection=connection,
            settings=settings,
        ),
        NORMALIZE_DART: DartNormalizeTaskHandler(connection),
    }
