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
    # 소스별 생성형 LLM은 판정 경로에서 제거됨(결정론 전처리 원칙 — docs/archive/design/worker-redesign.md).
    # DART 분석기는 규칙추출(+공시 임베딩)만 사용한다 — 끝단 SYNTHESIZE만이 유일한 생성형 LLM이다.
    # (Tier-C 정리: DART 분석 핸들러의 llm_analyzer 배관 제거. analyzers/dart/llm.py 의 클라이언트는
    # SYNTHESIZE 가 재사용하므로 보존한다.)
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
        ANALYZE_DART: DartAnalyzeTaskHandler(connection),
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
