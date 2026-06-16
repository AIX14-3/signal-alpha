from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.orchestrator.queue.task_types import (
    ANALYZE_ALTERNATIVE,
    ANALYZE_DART,
    COLLECT_DART,
    NORMALIZE_DART,
    NORMALIZE_DATALAB,
    NORMALIZE_HIRING,
    NORMALIZE_PATENT,
)
from app.orchestrator.queue.tasks import TaskHandler


def build_task_handlers(connection: Any) -> dict[str, TaskHandler]:
    from app.analyzers.dart.llm import DartLlmAnalyzer, GeminiGenerateContentClient, OpenAiChatClient
    from app.orchestrator.alternative.tasks import (
        AlternativeAnalyzeTaskHandler,
        DataLabNormalizeTaskHandler,
        HiringNormalizeTaskHandler,
        PatentNormalizeTaskHandler,
    )
    from app.orchestrator.dart.tasks import (
        DartAnalyzeTaskHandler,
        DartCollectionTaskHandler,
        DartNormalizeTaskHandler,
    )

    settings = get_settings()
    llm_analyzer = None
    if settings.dart_use_llm and settings.dart_llm_provider == "gemini" and settings.gemini_api_key and settings.dart_llm_model:
        llm_analyzer = DartLlmAnalyzer(
            client=GeminiGenerateContentClient(
                api_key=settings.gemini_api_key,
                base_url=settings.gemini_base_url,
            ),
            model=settings.dart_llm_model,
            timeout_seconds=settings.dart_llm_timeout_seconds,
        )
    elif settings.dart_use_llm and settings.dart_llm_provider == "openai" and settings.openai_api_key and settings.dart_llm_model:
        llm_analyzer = DartLlmAnalyzer(
            client=OpenAiChatClient(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
            ),
            model=settings.dart_llm_model,
            timeout_seconds=settings.dart_llm_timeout_seconds,
        )
    return {
        COLLECT_DART: DartCollectionTaskHandler(
            connection=connection,
            settings=settings,
        ),
        NORMALIZE_DART: DartNormalizeTaskHandler(connection),
        ANALYZE_DART: DartAnalyzeTaskHandler(
            connection,
            llm_analyzer=llm_analyzer,
            llm_high_impact_only=settings.dart_llm_high_impact_only,
        ),
        # Alternative sources (hiring/patent/datalab) — converged onto the queue.
        NORMALIZE_HIRING: HiringNormalizeTaskHandler(connection),
        NORMALIZE_PATENT: PatentNormalizeTaskHandler(connection),
        NORMALIZE_DATALAB: DataLabNormalizeTaskHandler(connection),
        ANALYZE_ALTERNATIVE: AlternativeAnalyzeTaskHandler(connection),
    }
