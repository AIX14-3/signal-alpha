from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.orchestrator.queue.task_types import (
    AGGREGATE_SIGNAL,
    ANALYZE_DART,
    ANALYZE_DATALAB,
    ANALYZE_HIRING,
    ANALYZE_PATENT,
    ANALYZE_PRICE,
    ANALYZE_REPORT,
    COLLECT_DART,
    COLLECT_REPORT,
    ENRICH_HIRING,
    ENRICH_PATENT,
    NORMALIZE_DART,
    NORMALIZE_DATALAB,
    NORMALIZE_HIRING,
    NORMALIZE_PATENT,
    NORMALIZE_REPORT,
    PROCESS_REPORT,
    PUBLISH_SIGNALS,
    RETURN_COMBINE,
    SRC_INFER,
    SYNTHESIZE,
)
from app.orchestrator.queue.tasks import TaskHandler


def build_task_handlers(connection: Any) -> dict[str, TaskHandler]:
    from app.analyzers.registry import registration_for
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
    from app.ml.return_combine import ReturnCombineTaskHandler
    from app.ml.source_inference import SrcInferTaskHandler
    from app.publish.publish_task import PublishSignalsTaskHandler
    from app.synthesis.tasks import SynthesizeTaskHandler

    settings = get_settings()
    # 숫자(방향/점수)는 결정론이 소유한다(불변식). LLM 은 근거만.
    # Wave 2: DART_USE_LLM=on + provider/model/key 설정 시에만 고임팩트 공시 '근거' 추출기를 배선한다
    # (verdict 아님 — direction/score 불변). 미설정(기본)이면 None → 규칙 피처만, 프로덕션 회귀 0.
    from app.agents.dart.evidence import build_dart_evidence_extractor

    dart_evidence_extractor = build_dart_evidence_extractor(settings)
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
            connection, evidence_extractor=dart_evidence_extractor
        ),
        ANALYZE_PRICE: PriceAnalyzeTaskHandler(connection),
        AGGREGATE_SIGNAL: AggregateSignalTaskHandler(connection),
        # 소스별 base 모델 추론(#525 Phase 3). run_key=SRC 로 분리 적재(D4). 성공 예측이
        # 있으면 RETURN_COMBINE 을 인큐해 return 채널을 결합한다.
        SRC_INFER: SrcInferTaskHandler(connection),
        # 메타러너 return 채널 결합(#525 WS-C) — src_* + Report 피처 → meta_signals return 컬럼.
        RETURN_COMBINE: ReturnCombineTaskHandler(connection),
        # 발행(#11) — 종목 PUBLISHED 테이블을 백엔드 DB 로 복사. BACKEND_DATABASE_URL 없으면 no-op.
        PUBLISH_SIGNALS: PublishSignalsTaskHandler(connection, settings=settings),
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
        # Per-source analysis stages (C안 Phase 3) — one handler per source, each
        # given its single-source registration so the shared handler analyzes ONE
        # source and publishes its own run_key signal.
        ANALYZE_HIRING: AlternativeAnalyzeTaskHandler(
            connection, registrations=[registration_for("HIRING")]
        ),
        ANALYZE_PATENT: AlternativeAnalyzeTaskHandler(
            connection, registrations=[registration_for("PATENT")]
        ),
        ANALYZE_DATALAB: AlternativeAnalyzeTaskHandler(
            connection, registrations=[registration_for("DATALAB")]
        ),
    }
