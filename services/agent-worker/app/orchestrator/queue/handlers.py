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
    EMBED_DART,
    EMBED_REPORT,
    ENRICH_PATENT,
    META_COMBINE,
    ML_INFER,
    NORMALIZE_DART,
    NORMALIZE_DATALAB,
    NORMALIZE_HIRING,
    NORMALIZE_PATENT,
    NORMALIZE_REPORT,
    PROCESS_REPORT,
    RISK_VETO,
    SYNTHESIZE,
)
from app.orchestrator.queue.tasks import TaskHandler


def build_task_handlers(connection: Any) -> dict[str, TaskHandler]:
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
        DartEmbedTaskHandler,
        DartNormalizeTaskHandler,
    )
    from app.orchestrator.aggregation.tasks import AggregateSignalTaskHandler
    from app.ml.inference import MlInferTaskHandler
    from app.ml.meta_combine import MetaCombineTaskHandler
    from app.gates.risk_veto import RiskVetoTaskHandler
    from app.synthesis.tasks import SynthesizeTaskHandler

    settings = get_settings()
    # 소스별 생성형 LLM은 판정 경로에서 제거됨(결정론 전처리 원칙 — docs/design/worker-redesign.md).
    # DART 분석기는 규칙추출(+공시 임베딩)만 사용한다. LLM 모듈(analyzers/dart/llm.py)은
    # 보존하되 와이어링하지 않는다 — 끝단 SYNTHESIZE만이 유일한 생성형 LLM이다.
    llm_analyzer = None
    from app.orchestrator.report.tasks import (
        ReportAnalyzeTaskHandler,
        ReportCollectTaskHandler,
        ReportEmbedTaskHandler,
        ReportNormalizeTaskHandler,
        ReportProcessTaskHandler,
    )
    from app.orchestrator.report.llm_wiring import build_report_llm_config

    report_llm_config = build_report_llm_config(settings)

    return {
        COLLECT_DART: DartCollectionTaskHandler(
            connection=connection,
            settings=settings,
        ),
        NORMALIZE_DART: DartNormalizeTaskHandler(connection),
        EMBED_DART: DartEmbedTaskHandler(connection),
        ANALYZE_DART: DartAnalyzeTaskHandler(
            connection,
            llm_analyzer=llm_analyzer,
            llm_high_impact_only=settings.dart_llm_high_impact_only,
        ),
        AGGREGATE_SIGNAL: AggregateSignalTaskHandler(connection),
        ML_INFER: MlInferTaskHandler(connection),
        META_COMBINE: MetaCombineTaskHandler(connection),
        RISK_VETO: RiskVetoTaskHandler(connection, settings=settings),
        SYNTHESIZE: SynthesizeTaskHandler(connection, settings=settings),
        COLLECT_REPORT: ReportCollectTaskHandler(connection=connection, settings=settings),
        PROCESS_REPORT: ReportProcessTaskHandler(connection=connection, settings=settings),
        NORMALIZE_REPORT: ReportNormalizeTaskHandler(connection=connection),
        EMBED_REPORT: ReportEmbedTaskHandler(connection=connection, settings=settings),
        ANALYZE_REPORT: ReportAnalyzeTaskHandler(
            connection=connection,
            settings=settings,
            llm_client=report_llm_config.client if report_llm_config else None,
            llm_model=report_llm_config.model if report_llm_config else None,
            llm_timeout_seconds=(
                report_llm_config.timeout_seconds if report_llm_config else settings.report_llm_timeout_seconds
            ),
        ),
        # Alternative sources (hiring/patent/datalab) — converged onto the queue.
        NORMALIZE_HIRING: HiringNormalizeTaskHandler(connection),
        NORMALIZE_PATENT: PatentNormalizeTaskHandler(connection),
        NORMALIZE_DATALAB: DataLabNormalizeTaskHandler(connection),
        ENRICH_PATENT: PatentEnrichTaskHandler(connection),
        ANALYZE_ALTERNATIVE: AlternativeAnalyzeTaskHandler(connection),
    }
