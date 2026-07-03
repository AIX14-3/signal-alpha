from __future__ import annotations

from typing import Any

from app.core.config import get_settings
from app.orchestrator.queue.task_types import (
    AGGREGATE_SIGNAL,
    REQUERY_SOURCE,
    ANALYZE_DART,
    ANALYZE_DATALAB,
    ANALYZE_HIRING,
    ANALYZE_PATENT,
    ANALYZE_PRICE,
    ANALYZE_REPORT,
    COLLECT_DART,
    COLLECT_DART_OWNERSHIP,
    COLLECT_REPORT,
    EMBED_REPORT,
    ENRICH_HIRING,
    ENRICH_PATENT,
    NORMALIZE_DART,
    NORMALIZE_DATALAB,
    NORMALIZE_HIRING,
    NORMALIZE_PATENT,
    NORMALIZE_REPORT,
    PROCESS_REPORT,
    PUBLISH_SIGNALS,
    RECORD_EPISODE_OUTCOMES,
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
        DartOwnershipCollectionTaskHandler,
    )
    from app.orchestrator.aggregation.tasks import AggregateSignalTaskHandler
    from app.orchestrator.aggregation.requery import RequerySourceTaskHandler
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
        ReportEmbedTaskHandler,
        ReportNormalizeTaskHandler,
        ReportProcessTaskHandler,
    )

    return {
        COLLECT_DART: DartCollectionTaskHandler(
            connection=connection,
            settings=settings,
        ),
        COLLECT_DART_OWNERSHIP: DartOwnershipCollectionTaskHandler(
            connection=connection,
            settings=settings,
        ),
        NORMALIZE_DART: DartNormalizeTaskHandler(connection),
        ANALYZE_DART: DartAnalyzeTaskHandler(
            connection, evidence_extractor=dart_evidence_extractor
        ),
        ANALYZE_PRICE: PriceAnalyzeTaskHandler(connection),
        AGGREGATE_SIGNAL: AggregateSignalTaskHandler(
            connection,
            episode_writer=_build_episode_writer(connection),
            episode_recall=_build_episode_recall(connection),
        ),
        # 오케스트레이터 조건부 되묻기(Wave-3 양방향) — 불일치 소스만 집중 재분석 후 재종합.
        REQUERY_SOURCE: RequerySourceTaskHandler(connection),
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
        # RAG 임베딩 적재(#709) — REPORT_USE_LLM=true 일 때 PROCESS 가 인큐. 하류 enqueue 없음.
        EMBED_REPORT: ReportEmbedTaskHandler(connection=connection, settings=settings),
        # ANALYZE 는 settings 를 받아 REPORT_USE_LLM 시 RAG 재해석을 method_detail.report_rag 로 가법.
        ANALYZE_REPORT: ReportAnalyzeTaskHandler(connection=connection, settings=settings),
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
        # 에피소드 아웃컴 리코더(Wave 3 후속②) — 발행 후 실현결과를 outcome 에 progressive 기록.
        # 종목 무관 배치 스윕. 드레인 데몬이 일 1회 재시드. 숫자 불변(recall 참고용).
        RECORD_EPISODE_OUTCOMES: _build_episode_outcome_handler(connection, settings),
    }


def _build_episode_outcome_handler(connection: Any, settings: Any) -> Any:
    from app.memory import EpisodeOutcomeTaskHandler

    return EpisodeOutcomeTaskHandler(connection, settings=settings)


def _build_embedder() -> Any | None:
    """Build the shared Gemini embedder for episodic memory, or None to degrade.

    No GEMINI_API_KEY (or a transport/init failure) → None → the aggregator runs
    with memory disabled (no recall/write), publishing unchanged. Kept private so
    the only wiring point is the handler factory.
    """
    if not get_settings().gemini_api_key:
        return None
    try:
        from app.clients.embedding_client import GeminiEmbeddingClient

        return GeminiEmbeddingClient()
    except Exception:  # noqa: BLE001 — missing key/transport: degrade, don't stall
        return None


def _build_episode_writer(connection: Any) -> Any | None:
    embedder = _build_embedder()
    if embedder is None:
        return None
    from app.memory import EpisodeWriter
    from signal_alpha_data_access.repositories import SignalEpisodeRepository

    return EpisodeWriter(embedder=embedder, repository=SignalEpisodeRepository(connection))


def _build_episode_recall(connection: Any) -> Any | None:
    embedder = _build_embedder()
    if embedder is None:
        return None
    from app.memory import EpisodeRecall
    from signal_alpha_data_access.repositories import SignalEpisodeRepository

    return EpisodeRecall(embedder=embedder, repository=SignalEpisodeRepository(connection))
