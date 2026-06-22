from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from app.agents import SourceAgentInput, SourceAnalysisAgent
from app.agents.dart.graph import DartAnalysisGraphAgent
from app.analyzers.dart.financials import extract_dart_financial_metrics
from app.analyzers.dart.llm import DartLlmAnalyzer
from app.analyzers.dart.rules import classify_dart_report, make_dart_event_hash
from app.analyzers.dart.embedding_features import mean_vector
from app.collectors.dart.disclosure import DartCollector, DartDisclosureClient
from app.collectors.report.parsers.chunker import chunk_text
from app.embeddings.provider import get_embedding_provider, to_pgvector
from app.orchestrator.persistence import CollectionPersistence
from app.orchestrator.queue.task_types import (
    AGGREGATE_SIGNAL,
    ANALYZE_DART,
    EMBED_DART,
    NORMALIZE_DART,
)
from signal_alpha_data_access.repositories import (
    AnalysisRepository,
    DartRepository,
    NormalizationRepository,
    ProcessingQueueRepository,
    RawDetailRepository,
)


class DartCollectionTaskHandler:
    def __init__(
        self,
        *,
        connection: Any,
        settings: Any,
        client: DartDisclosureClient | None = None,
    ) -> None:
        self._connection = connection
        self._settings = settings
        self._client = client

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        task_context = _task_context(task.get("task_context"))
        stock_code = _stock_code_from_context(task_context)
        dart_repository = DartRepository(self._connection)
        date_window = await _resolve_collection_window(
            repository=dart_repository,
            stock_code=stock_code,
            task_context=task_context,
        )
        if date_window.get("skip_reason"):
            return {
                "collector_run_id": None,
                "collected_count": 0,
                "inserted_count": 0,
                "raw_document_ids": [],
                "skipped_reason": date_window["skip_reason"],
                "last_end_de": date_window.get("last_end_de"),
                "end_de": date_window["end_de"],
            }

        collector = DartCollector(
            api_key=self._settings.dart_api_key,
            corp_code_repository=dart_repository,
            client=self._client or _dart_client_from_settings(self._settings),
            start_date=date_window["bgn_de"],
            end_date=date_window["end_de"],
            page_size=self._settings.dart_page_size,
            fetch_documents=getattr(self._settings, "dart_fetch_documents", True),
        )
        evidence = await collector.collect(stock_code)
        result = await CollectionPersistence(self._connection).save_evidence_batch(
            stock_id=stock_id,
            stock_code=stock_code,
            evidence=evidence,
            collector_type="DART",
            enqueue_task_type=NORMALIZE_DART,
            force_reprocess=_truthy(task_context.get("force_reprocess")),
        )
        await dart_repository.upsert_collection_state(
            stock_id=stock_id,
            ticker=stock_code,
            last_bgn_de=date_window["bgn_de"],
            last_end_de=date_window["end_de"],
            last_receipt_no=_last_receipt_no(evidence),
            last_collected_count=result["collected_count"],
            last_collector_run_id=result["collector_run_id"],
        )
        return result


class DartNormalizeTaskHandler:
    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._raw_detail_repository = RawDetailRepository(connection)
        self._normalization_repository = NormalizationRepository(connection)
        self._queue_repository = ProcessingQueueRepository(connection)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        task_context = _task_context(task.get("task_context"))
        stock_code = _stock_code_from_context(task_context)
        raw_document_ids = _source_raw_ids(task.get("source_raw_ids"))
        rows = await self._raw_detail_repository.list_dart_documents_by_raw_ids(raw_document_ids)

        signal_event_ids: list[int] = []
        analysis_task_ids: list[int] = []
        for row in rows:
            classification = classify_dart_report(
                row["report_name"],
                is_correction=_truthy(row.get("is_correction")),
            )
            evidence_text = _dart_evidence_text(row)
            source_document = await self._normalization_repository.upsert_source_document(
                raw_document_id=row["raw_document_id"],
                stock_id=row["stock_id"],
                source_type="DART",
                source_name=row["source_name"],
                title=row["title"],
                source_url=row["source_url"],
                published_at=row["published_at"],
                collected_at=row["collected_at"],
                reliability_level="high",
                is_official=True,
            )
            signal_event = await self._normalization_repository.upsert_signal_event(
                stock_id=row["stock_id"],
                source_document_id=source_document["id"],
                event_hash=make_dart_event_hash(stock_code, row["receipt_no"], row["report_name"]),
                source_type="DART",
                event_type=classification.event_type,
                event_date=_to_date(row["published_at"]),
                signal_direction=classification.signal_direction,
                impact_level=classification.impact_level,
                title=row["report_name"],
                summary=f"DART disclosure: {row['report_name']}",
                evidence_text=evidence_text,
                evidence_url=row["source_url"],
                needs_review=classification.needs_review,
            )
            signal_event_ids.append(signal_event["id"])
            await self._normalization_repository.upsert_signal_metric(
                signal_event_id=signal_event["id"],
                metric_name="dart_disclosure_count",
                metric_value=1,
                metric_unit="count",
            )
            for metric in extract_dart_financial_metrics(evidence_text):
                await self._normalization_repository.upsert_signal_metric(
                    signal_event_id=signal_event["id"],
                    metric_name=metric["metric_name"],
                    metric_value=metric["metric_value"],
                    metric_unit=metric["metric_unit"],
                )
            await self._normalization_repository.record_validation_log(
                target_type="signal_event",
                target_id_int=signal_event["id"],
                validation_type="source_trace",
                passed=True,
                message=f"Normalized from raw_document_id={row['raw_document_id']}",
            )
            analysis_task_id = await self._queue_repository.enqueue(
                stock_id=int(row["stock_id"]),
                task_type=ANALYZE_DART,
                priority="batch",
                source_signal_event_ids=[int(signal_event["id"])],
                task_context={
                    "stock_code": stock_code,
                    "source_type": "DART",
                    "run_key": f"DART_EVENT_{signal_event['id']}",
                },
                dedupe=True,
            )
            analysis_task_ids.append(analysis_task_id)
            # 결정론 전처리: 공시 본문을 BGE-M3로 임베딩해 dart_chunks 적재(생성형 LLM 미사용).
            # 파싱/임베딩 실패를 분석과 격리하고 독립 재시도하도록 별도 EMBED_DART 태스크로 분리.
            await self._queue_repository.enqueue(
                stock_id=int(row["stock_id"]),
                task_type=EMBED_DART,
                priority="batch",
                task_context={
                    "stock_code": stock_code,
                    "source_type": "DART",
                    "raw_document_id": int(row["raw_document_id"]),
                },
                dedupe=True,
            )

        return {
            "normalized_count": len(rows),
            "signal_event_ids": signal_event_ids,
            "analysis_task_id": analysis_task_ids[0] if analysis_task_ids else None,
            "analysis_task_ids": analysis_task_ids,
        }


class DartEmbedTaskHandler:
    """DART 공시 본문 임베딩(결정론 전처리) — BGE-M3(1024d) → dart_chunks 적재.

    1. task_context.raw_document_id 로 공시 원문(extra_payload.document_text 우선,
       없으면 report_name)을 회수
    2. report와 동일한 chunker로 분할 → BGE-M3로 임베딩(고정 가중치 = 결정론)
    3. dart_chunks 적재. UNIQUE(raw_document_id, chunk_index) → ON CONFLICT DO NOTHING 으로 멱등.

    생성형 LLM은 쓰지 않는다(docs/design/worker-redesign.md). 적재된 임베딩은 추후
    DART 분석기의 결정론 피처(유사도·신규성 등)로 소비된다.
    """

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._raw_detail_repository = RawDetailRepository(connection)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        task_context = _task_context(task.get("task_context"))
        raw_document_id = task_context.get("raw_document_id")
        if raw_document_id is None:
            return {"status": "skipped", "reason": "raw_document_id_required"}
        raw_document_id = int(raw_document_id)

        rows = await self._raw_detail_repository.list_dart_documents_by_raw_ids([raw_document_id])
        if not rows:
            return {"status": "not_found", "raw_document_id": raw_document_id}
        row = rows[0]

        # 본문이 있을 때만 임베딩한다 — 제목만 있는 공시(report_name 폴백)는 임베딩
        # 가치가 낮고 BGE-M3 호출만 낭비하므로 건너뛴다.
        full_text = _dart_document_text(row)
        if not full_text:
            return {"status": "no_document_text", "raw_document_id": raw_document_id}
        chunks = chunk_text(full_text)
        if not chunks:
            return {"status": "no_text", "raw_document_id": raw_document_id}

        embeddings = await get_embedding_provider().embed(chunks)
        stock_id = int(row["stock_id"])
        for idx, (chunk, embedding) in enumerate(zip(chunks, embeddings)):
            await self._connection.execute(
                """
                INSERT INTO dart_chunks (
                    raw_document_id, stock_id, chunk_index,
                    chunk_text, token_count, embedding
                ) VALUES ($1, $2, $3, $4, $5, $6::vector)
                ON CONFLICT (raw_document_id, chunk_index) DO NOTHING
                """,
                raw_document_id,
                stock_id,
                idx,
                chunk,
                len(chunk.split()),
                to_pgvector(embedding),
            )

        novelty = await self._upsert_document_features(
            raw_document_id=raw_document_id,
            stock_id=stock_id,
            embeddings=embeddings,
        )

        return {
            "status": "embedded",
            "raw_document_id": raw_document_id,
            "chunks": len(chunks),
            "mean_prior_distance": novelty,
        }

    async def _upsert_document_features(
        self,
        *,
        raw_document_id: int,
        stock_id: int,
        embeddings: list[list[float]],
    ) -> float | None:
        """결정론 임베딩 피처(신규성)를 dart_document_features에 적재.

        현재 공시 중심벡터 ↔ 같은 종목의 선적재 공시 청크들의 평균 코사인거리(pgvector
        `<=>`)를 DB에서 계산한다(1024차원 raw를 모델에 직접 넣지 않음 = 과적합 방지).
        과거 공시가 없으면 거리 NULL(insufficient_history). 같은 입력·적재순서 → 같은 값.
        """
        centroid = mean_vector(embeddings)
        if not centroid:
            return None
        feature_row = await self._connection.fetchrow(
            """
            SELECT AVG(embedding <=> $1::vector) AS mean_prior_distance,
                   COUNT(*) AS prior_chunk_count
            FROM dart_chunks
            WHERE stock_id = $2 AND raw_document_id <> $3
            """,
            to_pgvector(centroid),
            stock_id,
            raw_document_id,
        )
        mean_prior_distance = feature_row["mean_prior_distance"] if feature_row else None
        prior_chunk_count = (
            int(feature_row["prior_chunk_count"])
            if feature_row and feature_row["prior_chunk_count"] is not None
            else 0
        )
        await self._connection.execute(
            """
            INSERT INTO dart_document_features (
                raw_document_id, stock_id, mean_prior_distance, prior_chunk_count
            ) VALUES ($1, $2, $3, $4)
            ON CONFLICT (raw_document_id) DO UPDATE
                SET mean_prior_distance = EXCLUDED.mean_prior_distance,
                    prior_chunk_count = EXCLUDED.prior_chunk_count
            """,
            raw_document_id,
            stock_id,
            float(mean_prior_distance) if mean_prior_distance is not None else None,
            prior_chunk_count,
        )
        return float(mean_prior_distance) if mean_prior_distance is not None else None


class DartAnalyzeTaskHandler:
    def __init__(
        self,
        connection: Any,
        *,
        llm_analyzer: DartLlmAnalyzer | None = None,
        llm_high_impact_only: bool = True,
        analysis_agent: SourceAnalysisAgent | None = None,
    ) -> None:
        self._normalization_repository = NormalizationRepository(connection)
        self._analysis_repository = AnalysisRepository(connection)
        self._queue_repository = ProcessingQueueRepository(connection)
        self._analysis_agent = analysis_agent or DartAnalysisGraphAgent(
            llm_analyzer=llm_analyzer,
            llm_high_impact_only=llm_high_impact_only,
        )

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        task_context = _task_context(task.get("task_context"))
        signal_event_ids = _source_signal_event_ids(task.get("source_signal_event_ids"))
        if not signal_event_ids:
            return {
                "analyzed_count": 0,
                "analysis_result_id": None,
                "agent_result_id": None,
                "skipped_reason": "source_signal_event_ids_required",
            }

        rows = await self._normalization_repository.list_signal_events_by_ids(signal_event_ids)
        events = [dict(row) for row in rows]
        signal_event_ids = [int(event["id"]) for event in events]
        result = await self._analysis_agent.analyze(
            SourceAgentInput(
                source="DART",
                stock_code=str(task_context.get("stock_code") or ""),
                stock_id=stock_id,
                analysis_date=_analysis_date(events, task_context),
                run_key=_run_key(task_context),
                events=events,
                context=task_context,
            )
        )
        analysis_date = _analysis_date(events, task_context)
        run_key = _run_key(task_context)

        analysis_result = await self._analysis_repository.upsert_analysis_result(
            stock_id=stock_id,
            analysis_date=analysis_date,
            run_key=run_key,
            source_signal_event_ids=signal_event_ids,
            base_score=_to_db_score(result.score),
            analysis_mode="dart_only",
            warning="; ".join(result.risk_flags) or None,
            version=result.prompt_ver,
        )
        agent_result = await self._analysis_repository.upsert_agent_result(
            result_id=analysis_result["id"],
            stock_id=stock_id,
            debate_method="D-1",
            source_signal_event_ids=signal_event_ids,
            method_score=_to_db_score(result.score),
            method_signal=result.direction,
            method_detail={
                **result.method_detail,
                "source_score": result.score,
                "summary": result.summary,
                "risk_flags": result.risk_flags,
                "needs_review": result.needs_review,
                "stock_code": task_context.get("stock_code"),
                "analysis_source": result.analysis_source,
                **({"llm_error": result.llm_error} if result.llm_error else {}),
            },
            reliability_score=90,
            evidence_quality=_evidence_quality(events),
            llm_model=result.llm_model,
            prompt_ver=result.prompt_ver,
        )
        aggregate_task_id = await self._queue_repository.enqueue(
            stock_id=stock_id,
            task_type=AGGREGATE_SIGNAL,
            priority="batch",
            source_analysis_result_ids=[int(analysis_result["id"])],
            task_context={
                "stock_code": task_context.get("stock_code"),
                "signal_date": analysis_date.isoformat(),
                "run_key": "AGGREGATED",
                "aggregation_key": f"AGGREGATED:{stock_id}:{analysis_date.isoformat()}:final-agg-v1",
            },
            dedupe=True,
        )
        return {
            "analysis_result_id": analysis_result["id"],
            "agent_result_id": agent_result["id"],
            "aggregate_task_id": aggregate_task_id,
            "analyzed_count": len(events),
            "direction": result.direction,
            "score": result.score,
            "needs_review": result.needs_review,
            "analysis_source": result.analysis_source,
        }


def _stock_code_from_context(task_context: dict[str, Any]) -> str:
    stock_code = task_context.get("stock_code") or task_context.get("ticker")
    if not stock_code:
        raise ValueError("collect_dart task_context.stock_code is required.")
    return str(stock_code).strip()


def _task_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _source_raw_ids(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list):
        return [int(item) for item in value]
    if isinstance(value, tuple):
        return [int(item) for item in value]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") and text.endswith("}"):
            inner = text[1:-1].strip()
            if not inner:
                return []
            return [int(item.strip()) for item in inner.split(",")]
        parsed = json.loads(text)
        return [int(item) for item in parsed]
    return [int(value)]


def _source_signal_event_ids(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list):
        return [int(item) for item in value]
    if isinstance(value, tuple):
        return [int(item) for item in value]
    if isinstance(value, str):
        text = value.strip()
        if text.startswith("{") and text.endswith("}"):
            inner = text[1:-1].strip()
            if not inner:
                return []
            return [int(item.strip()) for item in inner.split(",")]
        parsed = json.loads(text)
        return [int(item) for item in parsed]
    return [int(value)]


async def _resolve_collection_window(
    *,
    repository: DartRepository,
    stock_code: str,
    task_context: dict[str, Any],
) -> dict[str, str]:
    end_de = _yyyymmdd(task_context.get("end_de")) or _today_yyyymmdd()
    bgn_de = _yyyymmdd(task_context.get("bgn_de"))
    if bgn_de:
        return {"bgn_de": bgn_de, "end_de": end_de}

    state = await repository.get_collection_state_by_ticker(stock_code)
    if state and state.get("last_end_de"):
        next_bgn_de = _next_yyyymmdd(state["last_end_de"])
        if next_bgn_de > end_de:
            return {
                "bgn_de": next_bgn_de,
                "end_de": end_de,
                "last_end_de": _yyyymmdd(state["last_end_de"]),
                "skip_reason": "dart_collection_up_to_date",
            }
        return {
            "bgn_de": next_bgn_de,
            "end_de": end_de,
        }
    return {"bgn_de": _default_bgn_de(end_de), "end_de": end_de}


def _last_receipt_no(evidence: list[Any]) -> str | None:
    if not evidence:
        return None
    return evidence[-1].metadata.get("receipt_no")


def _yyyymmdd(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, datetime):
        return value.strftime("%Y%m%d")
    if isinstance(value, date):
        return value.strftime("%Y%m%d")
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return text
    return datetime.fromisoformat(text[:10]).strftime("%Y%m%d")


def _next_yyyymmdd(value: Any) -> str:
    parsed = datetime.strptime(_yyyymmdd(value), "%Y%m%d").date()
    return (parsed + timedelta(days=1)).strftime("%Y%m%d")


def _default_bgn_de(end_de: str) -> str:
    parsed = datetime.strptime(end_de, "%Y%m%d").date()
    return (parsed - timedelta(days=30)).strftime("%Y%m%d")


def _today_yyyymmdd() -> str:
    return date.today().strftime("%Y%m%d")


def _dart_client_from_settings(settings: Any) -> DartDisclosureClient:
    return DartDisclosureClient(
        api_key=settings.dart_api_key,
        base_url=getattr(settings, "dart_base_url", "https://opendart.fss.or.kr/api"),
        timeout_seconds=getattr(settings, "dart_timeout_seconds", 10),
        max_retries=getattr(settings, "dart_max_retries", 2),
        retry_backoff_seconds=getattr(settings, "dart_retry_backoff_seconds", 0.5),
    )


def _dart_document_text(row: Mapping[str, Any]) -> str | None:
    """공시 본문(extra_payload.document_text)만 반환. 본문이 없으면 None.

    임베딩은 본문이 있을 때만 의미가 있으므로 제목(report_name) 폴백을 쓰지 않는다.
    """
    extra_payload = row.get("extra_payload") or {}
    if isinstance(extra_payload, str):
        extra_payload = json.loads(extra_payload)
    document_text = extra_payload.get("document_text")
    return str(document_text) if document_text else None


def _dart_evidence_text(row: Mapping[str, Any]) -> str:
    return _dart_document_text(row) or str(row["report_name"])


def _truthy(value: Any) -> bool:
    return str(value).lower() in {"1", "true", "yes", "y"}


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value)
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    return datetime.fromisoformat(text[:10]).date()


def _analysis_date(events: list[dict[str, Any]], task_context: dict[str, Any]) -> date:
    if not events:
        return _task_analysis_date(task_context)
    event_dates = [_to_date(event["event_date"]) for event in events if event.get("event_date")]
    if not event_dates:
        return _task_analysis_date(task_context)
    return max(event_dates)


def _task_analysis_date(task_context: dict[str, Any]) -> date:
    value = task_context.get("analysis_date") or task_context.get("event_date")
    if value:
        return _to_date(value)
    return date.today()


def _run_key(task_context: dict[str, Any]) -> str:
    return str(task_context.get("run_key") or "DART").strip() or "DART"


def _evidence_quality(events: list[dict[str, Any]]) -> int:
    if not events:
        return 0
    official_count = sum(1 for event in events if event.get("is_official"))
    return round((official_count / len(events)) * 100)


def _to_db_score(score: float) -> float:
    """Map DART source-agent [-1, +1] score onto legacy DB 0-100 score columns."""
    return round(max(0.0, min(100.0, (score + 1.0) * 50.0)), 2)
