from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.orchestrator.queue.task_types import (
    AGGREGATE_SIGNAL,
    ANALYZE_ALTERNATIVE,
    ANALYZE_DART,
    ANALYZE_PRICE,
    ANALYZE_REPORT,
    COLLECT_DART,
    COLLECT_REPORT,
    ENRICH_HIRING,
    ENRICH_PATENT,
    META_COMBINE,
    ML_INFER,
    NORMALIZE_DART,
    NORMALIZE_DATALAB,
    NORMALIZE_HIRING,
    NORMALIZE_PATENT,
    NORMALIZE_REPORT,
    PROCESS_REPORT,
    PUBLISH_SIGNALS,
    RETURN_COMBINE,
    RISK_VETO,
    SRC_INFER,
    SYNTHESIZE,
)
from app.orchestrator.queue.tasks import TaskHandler


def build_task_handlers(connection: Any) -> dict[str, TaskHandler]:
    from app.orchestrator.alternative.tasks import (
        AlternativeAnalyzeTaskHandler,
        DataLabNormalizeTaskHandler,
        HiringNormalizeTaskHandler,
        HiringSkillEnrichTaskHandler,
        PatentEnrichTaskHandler,
        PatentNormalizeTaskHandler,
    )
    from app.orchestrator.dart.tasks import (
        DartAnalyzeTaskHandler,
        DartCollectionTaskHandler,
        DartNormalizeTaskHandler,
    )
    from app.orchestrator.aggregation.tasks import AggregateSignalTaskHandler
    from app.orchestrator.price.tasks import PriceAnalyzeTaskHandler
    from app.ml.inference import MlInferTaskHandler
    from app.ml.meta_combine import MetaCombineTaskHandler
    from app.ml.return_combine import ReturnCombineTaskHandler
    from app.ml.source_inference import SrcInferTaskHandler
    from app.publish.publish_task import PublishSignalsTaskHandler
    from app.gates.risk_veto import RiskVetoTaskHandler
    from app.synthesis.tasks import SynthesizeTaskHandler

    settings = get_settings()
    # 소스별 생성형 LLM은 판정 경로에서 제거됨(결정론 전처리 원칙 — docs/archive/design/worker-redesign.md).
    # DART 분석기는 규칙추출(+공시 임베딩)만 사용한다. LLM 모듈(analyzers/dart/llm.py)은
    # 보존하되 와이어링하지 않는다 — 끝단 SYNTHESIZE만이 유일한 생성형 LLM이다.
    llm_analyzer = None
    from app.orchestrator.report.tasks import (
        ReportCollectTaskHandler,
        ReportAnalyzeTaskHandler,
        ReportNormalizeTaskHandler,
        ReportProcessTaskHandler,
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
        ANALYZE_PRICE: PriceAnalyzeTaskHandler(connection),
        AGGREGATE_SIGNAL: AggregateSignalTaskHandler(connection),
        ML_INFER: MlInferTaskHandler(connection),
        META_COMBINE: MetaCombineTaskHandler(connection),
        # 소스별 base 모델 추론(#525 Phase 3). run_key=SRC 로 분리 적재(D4). 성공 예측이
        # 있으면 RETURN_COMBINE 을 인큐해 return 채널을 결합한다.
        SRC_INFER: SrcInferTaskHandler(connection),
        # 메타러너 return 채널 결합(#525 WS-C) — src_* + Report 피처 → meta_signals return 컬럼.
        RETURN_COMBINE: ReturnCombineTaskHandler(connection),
        # 발행(#11) — 종목 PUBLISHED 테이블을 백엔드 DB 로 복사. BACKEND_DATABASE_URL 없으면 no-op.
        PUBLISH_SIGNALS: PublishSignalsTaskHandler(connection, settings=settings),
        RISK_VETO: RiskVetoTaskHandler(connection, settings=settings),
        SYNTHESIZE: SynthesizeTaskHandler(connection, settings=settings),
        COLLECT_REPORT: ReportCollectTaskHandler(connection=connection, settings=settings),
        PROCESS_REPORT: ReportProcessTaskHandler(connection=connection, settings=settings),
        NORMALIZE_REPORT: ReportNormalizeTaskHandler(connection=connection),
        ANALYZE_REPORT: ReportAnalyzeTaskHandler(connection=connection),
        # Alternative sources (hiring/patent/datalab) — converged onto the queue.
        NORMALIZE_HIRING: HiringNormalizeTaskHandler(connection),
        NORMALIZE_PATENT: PatentNormalizeTaskHandler(connection),
        NORMALIZE_DATALAB: DataLabNormalizeTaskHandler(connection),
        ENRICH_PATENT: PatentEnrichTaskHandler(connection),
        ENRICH_HIRING: HiringSkillEnrichTaskHandler(connection),
        ANALYZE_ALTERNATIVE: AlternativeAnalyzeTaskHandler(connection),
    }
