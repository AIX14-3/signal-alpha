from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.orchestrator.queue.task_types import (
    AGGREGATE_SIGNAL,
    ANALYZE_ALTERNATIVE,
    ANALYZE_DART,
    ANALYZE_REPORT,
    COLLECT_DART,
    COLLECT_REPORT,
    EMBED_REPORT,
    ENRICH_PATENT,
    META_COMBINE,
    ML_INFER,
    NORMALIZE_DART,
    NORMALIZE_DATALAB,
    NORMALIZE_HIRING,
    NORMALIZE_PATENT,
    PROCESS_REPORT,
    RISK_VETO,
    SYNTHESIZE,
)
from app.orchestrator.queue.tasks import TaskHandler


def build_task_handlers(connection: Any) -> dict[str, TaskHandler]:
    from app.analyzers.dart.llm import DartLlmAnalyzer, GeminiGenerateContentClient, OpenAiChatClient
    from app.orchestrator.alternative.tasks import (
        AlternativeAnalyzeTaskHandler,
        DataLabNormalizeTaskHandler,
        HiringNormalizeTaskHandler,
        PatentEnrichTaskHandler,
        PatentNormalizeTaskHandler,
    )
    from app.orchestrator.dart.tasks import (
        DartAnalyzeTaskHandler,
        DartCollectionTaskHandler,
        DartNormalizeTaskHandler,
    )
    from app.orchestrator.aggregation.tasks import AggregateSignalTaskHandler
    from app.ml.inference import MlInferTaskHandler
    from app.ml.meta_combine import MetaCombineTaskHandler
    from app.gates.risk_veto import RiskVetoTaskHandler
    from app.synthesis.tasks import SynthesizeTaskHandler

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
    from app.orchestrator.report.tasks import (
        ReportAnalyzeTaskHandler,
        ReportCollectTaskHandler,
        ReportEmbedTaskHandler,
        ReportProcessTaskHandler,
    )

    # Report RAG agent LLM — provider/key는 dart와 동일한 openai/gemini 클라이언트 재사용.
    report_llm_client = None
    report_llm_model = None
    if settings.report_use_llm and settings.report_llm_model:
        if settings.report_llm_provider == "gemini" and settings.gemini_api_key:
            report_llm_client = GeminiGenerateContentClient(
                api_key=settings.gemini_api_key,
                base_url=settings.gemini_base_url,
            )
            report_llm_model = settings.report_llm_model
        elif settings.report_llm_provider == "openai" and settings.openai_api_key:
            report_llm_client = OpenAiChatClient(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
            )
            report_llm_model = settings.report_llm_model

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
        AGGREGATE_SIGNAL: AggregateSignalTaskHandler(connection),
        ML_INFER: MlInferTaskHandler(connection),
        META_COMBINE: MetaCombineTaskHandler(connection),
        RISK_VETO: RiskVetoTaskHandler(connection),
        SYNTHESIZE: SynthesizeTaskHandler(connection, settings=settings),
        COLLECT_REPORT: ReportCollectTaskHandler(connection=connection, settings=settings),
        PROCESS_REPORT: ReportProcessTaskHandler(connection=connection, settings=settings),
        EMBED_REPORT: ReportEmbedTaskHandler(connection=connection, settings=settings),
        ANALYZE_REPORT: ReportAnalyzeTaskHandler(
            connection=connection,
            settings=settings,
            llm_client=report_llm_client,
            llm_model=report_llm_model,
        ),
        # Alternative sources (hiring/patent/datalab) — converged onto the queue.
        NORMALIZE_HIRING: HiringNormalizeTaskHandler(connection),
        NORMALIZE_PATENT: PatentNormalizeTaskHandler(connection),
        NORMALIZE_DATALAB: DataLabNormalizeTaskHandler(connection),
        ENRICH_PATENT: PatentEnrichTaskHandler(connection),
        ANALYZE_ALTERNATIVE: AlternativeAnalyzeTaskHandler(connection),
    }
