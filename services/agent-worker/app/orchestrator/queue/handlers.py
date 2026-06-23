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
        ReportProcessTaskHandler,
    )

    # Report 분석기도 동일 — RAG 검색(임베딩)은 근거 회수로 유지하되, 소스단 생성 요약은
    # 끝단 SYNTHESIZE로 일원화한다(생성 LLM 미와이어링). 회수된 청크는 근거/피처로 적재된다.
    report_llm_client = None
    report_llm_model = None

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
