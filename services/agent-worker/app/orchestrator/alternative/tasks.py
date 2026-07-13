"""Queue task handlers for the Alternative sources (hiring / patent / datalab).

Converges the Alternative sources onto the same queue model DART uses
(``orchestrator/dart/tasks.py``):

  NORMALIZE_HIRING / NORMALIZE_PATENT
      raw detail rows -> source_documents + signal_events + signal_metrics
      -> (ENRICH_HIRING / ENRICH_PATENT) -> enqueue ANALYZE_HIRING / ANALYZE_PATENT
      (per stock, deduped by stock + as_of)
  NORMALIZE_DATALAB (router; no signal_events — DataLab raw lives in a separate
      datalab_raw_documents table with no source_documents FK anchor)
      category_id -> datalab_category_stocks -> enqueue ANALYZE_DATALAB
  ANALYZE_DATALAB / ANALYZE_HIRING / ANALYZE_PATENT (per stock, ONE source each)
      reuses build_registry()/loaders/analyzers; each source's SourceResult is
      mapped to its OWN signal (build_source_signal) and persisted under its own
      run_key via AlternativeSignalPersistence — no cross-source merge. The three
      sources are peer signals (like DART), each its own pipeline stage (C안 Phase 3).

The analysis granularity is per-stock-per-source (windowed): the Alternative
analyzers score aggregated windows, and each ANALYZE_{SOURCE} task publishes one
final_signals row for that source+stock.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from dataclasses import fields
from datetime import date, datetime
from typing import Any, Callable, Sequence

from app.agents.base import SourceAgentInput, SourceAgentOutput
from app.agents.rule_source_agent import RuleSourceAgent
from app.aggregator.per_source import build_source_signal
from app.analyzers.config import AggregatorConfig, AnalyzerRuntimeConfig
from app.core.config import get_settings
from app.enrichment.hiring_skills import HiringSkillEnricher
from app.enrichment.patent_features import PatentEnricher
from app.analyzers.datalab.normalize_rules import classify_datalab_observation
from app.analyzers.hiring.normalize_rules import classify_hiring_posting
from app.analyzers.patent.normalize_rules import classify_patent_filing
from app.analyzers.registry import SourceRegistration, build_registry
from app.orchestrator.alternative_persistence import AlternativeSignalPersistence
from app.orchestrator.queue.task_types import (
    ANALYZE_DATALAB,
    ANALYZE_HIRING,
    ANALYZE_PATENT,
    ENRICH_HIRING,
    ENRICH_PATENT,
)
from app.schemas.source_result import (
    EvidenceItem,
    HiringMeta,
    PatentMeta,
    ReportMeta,
    SourceResult,
)
from app.utils.hash_utils import make_source_hash
from signal_alpha_data_access.repositories import (
    NormalizationRepository,
    ProcessingQueueRepository,
    RawDetailRepository,
)

logger = logging.getLogger(__name__)

# Each Alternative source routes to its own ANALYZE_{SOURCE} stage (C안 Phase 3).
# Keyed by the producer's ``SOURCE_TYPE`` so a normalize/enrich handler enqueues
# the matching per-source analysis. A new source adds one entry here.
ANALYZE_TASK_BY_SOURCE = {
    "HIRING": ANALYZE_HIRING,
    "PATENT": ANALYZE_PATENT,
    "DATALAB": ANALYZE_DATALAB,
}


class _AltNormalizeBase:
    """Shared normalize flow for raw sources anchored in ``raw_documents``.

    Subclasses provide the source type, the raw-detail loader, and the
    per-document classification; the base handles source_document/signal_event
    upserts and the per-stock ANALYZE_{SOURCE} enqueue.
    """

    SOURCE_TYPE: str = ""
    RELIABILITY_LEVEL: str = "medium"
    IS_OFFICIAL: bool = False

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._raw_detail_repository = RawDetailRepository(connection)
        self._normalization_repository = NormalizationRepository(connection)
        self._queue_repository = ProcessingQueueRepository(connection)

    async def _load_rows(self, raw_document_ids: list[int]) -> list[Any]:
        raise NotImplementedError

    def _event_seed(self, row: Mapping[str, Any]) -> str:
        raise NotImplementedError

    def _classify(self, row: Mapping[str, Any]) -> Any:
        raise NotImplementedError

    def _event_date(self, row: Mapping[str, Any]) -> date:
        return _to_date(row["published_at"])

    def _title(self, row: Mapping[str, Any]) -> str:
        return str(row.get("title") or self.SOURCE_TYPE)

    def _summary(self, row: Mapping[str, Any]) -> str:
        return f"{self.SOURCE_TYPE} document: {self._title(row)}"

    def _evidence_text(self, row: Mapping[str, Any]) -> str:
        return self._title(row)

    def _metrics(self, row: Mapping[str, Any]) -> list[dict[str, Any]]:
        return []

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        task_context = _task_context(task.get("task_context"))
        as_of = _as_of_str(task_context.get("as_of"))
        raw_document_ids = _source_raw_ids(task.get("source_raw_ids"))
        rows = await self._load_rows(raw_document_ids)

        signal_event_ids: list[int] = []
        stock_ids: list[int] = []
        for row in rows:
            stock_id = int(row["stock_id"])
            source_document = await self._normalization_repository.upsert_source_document(
                raw_document_id=int(row["raw_document_id"]),
                stock_id=stock_id,
                source_type=self.SOURCE_TYPE,
                source_name=str(row["source_name"]),
                title=self._title(row),
                source_url=row.get("source_url"),
                published_at=row["published_at"],
                collected_at=row["collected_at"],
                reliability_level=self.RELIABILITY_LEVEL,
                is_official=self.IS_OFFICIAL,
            )
            classification = self._classify(row)
            signal_event = await self._normalization_repository.upsert_signal_event(
                stock_id=stock_id,
                source_document_id=source_document["id"],
                event_hash=make_source_hash(self.SOURCE_TYPE, self._event_seed(row)),
                source_type=self.SOURCE_TYPE,
                event_type=classification.event_type,
                event_date=self._event_date(row),
                signal_direction=classification.signal_direction,
                impact_level=classification.impact_level,
                title=self._title(row),
                summary=self._summary(row),
                evidence_text=self._evidence_text(row),
                evidence_url=row.get("source_url"),
                needs_review=classification.needs_review,
            )
            signal_event_ids.append(int(signal_event["id"]))
            stock_ids.append(stock_id)
            for metric in self._metrics(row):
                await self._normalization_repository.upsert_signal_metric(
                    signal_event_id=int(signal_event["id"]),
                    metric_name=metric["metric_name"],
                    metric_value=metric["metric_value"],
                    metric_unit=metric.get("metric_unit"),
                )
            await self._normalization_repository.record_validation_log(
                target_type="signal_event",
                target_id_int=int(signal_event["id"]),
                validation_type="source_trace",
                passed=True,
                message=f"Normalized from raw_document_id={row['raw_document_id']}",
            )

        followup_task_ids = await self._enqueue_followups(
            stock_ids=stock_ids,
            raw_document_ids=[int(row["raw_document_id"]) for row in rows],
            as_of=as_of,
        )
        return {
            "normalized_count": len(rows),
            "signal_event_ids": signal_event_ids,
            "analysis_task_ids": followup_task_ids,
        }

    async def _enqueue_followups(
        self,
        *,
        stock_ids: list[int],
        raw_document_ids: list[int],
        as_of: str,
    ) -> list[int]:
        """Enqueue what runs after normalization. Default: the per-stock analysis.

        PATENT overrides this to insert an LLM enrichment step (ENRICH_PATENT)
        ahead of analysis; ``raw_document_ids`` are the rows this task just
        normalized, so enrichment is scoped to them.
        """
        return await _enqueue_analysis_for_stocks(
            queue_repository=self._queue_repository,
            stock_ids=stock_ids,
            as_of=as_of,
            task_type=ANALYZE_TASK_BY_SOURCE[self.SOURCE_TYPE],
        )


class HiringNormalizeTaskHandler(_AltNormalizeBase):
    SOURCE_TYPE = "HIRING"
    RELIABILITY_LEVEL = "medium"
    IS_OFFICIAL = False

    async def _load_rows(self, raw_document_ids: list[int]) -> list[Any]:
        return await self._raw_detail_repository.list_hiring_details_by_raw_ids(raw_document_ids)

    def _event_seed(self, row: Mapping[str, Any]) -> str:
        # external_id is the job_link (UNIQUE per posting); stable across reruns.
        return str(row.get("external_id") or row["raw_document_id"])

    def _classify(self, row: Mapping[str, Any]) -> Any:
        return classify_hiring_posting(row)

    def _evidence_text(self, row: Mapping[str, Any]) -> str:
        keyword = row.get("keyword")
        category = row.get("job_category")
        parts = [str(p) for p in (self._title(row), keyword, category) if p]
        return " / ".join(dict.fromkeys(parts))

    def _metrics(self, row: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "metric_name": "hiring_job_count",
                "metric_value": int(row.get("job_count") or 1),
                "metric_unit": "count",
            }
        ]

    async def _enqueue_followups(
        self,
        *,
        stock_ids: list[int],
        raw_document_ids: list[int],
        as_of: str,
    ) -> list[int]:
        """Insert OCR enrichment before analysis: enqueue ENRICH_HIRING per stock.

        Each ENRICH_HIRING carries the just-normalized ``raw_document_ids`` and
        enqueues the per-stock ANALYZE_HIRING once the poster skills are
        cached, so the hiring score sees the freshly extracted tech demand. With
        nothing normalized there is nothing to enrich, so fall back to the plain
        analysis enqueue (keeps an empty run from stalling the pipeline). Mirrors
        ``PatentNormalizeTaskHandler._enqueue_followups``.
        """
        if not raw_document_ids:
            return await _enqueue_analysis_for_stocks(
                queue_repository=self._queue_repository,
                stock_ids=stock_ids,
                as_of=as_of,
                task_type=ANALYZE_HIRING,
            )
        task_ids: list[int] = []
        for stock_id in dict.fromkeys(stock_ids):  # distinct, order-preserving
            task_id = await self._queue_repository.enqueue(
                stock_id=stock_id,
                task_type=ENRICH_HIRING,
                priority="batch",
                source_raw_ids=raw_document_ids,
                task_context={"as_of": as_of, "source_type": "HIRING"},
                dedupe=True,
            )
            task_ids.append(task_id)
        return task_ids


class PatentNormalizeTaskHandler(_AltNormalizeBase):
    SOURCE_TYPE = "PATENT"
    # Patent filings are official government (KIPRIS) records.
    RELIABILITY_LEVEL = "high"
    IS_OFFICIAL = True

    async def _load_rows(self, raw_document_ids: list[int]) -> list[Any]:
        return await self._raw_detail_repository.list_patent_details_by_raw_ids(raw_document_ids)

    def _event_seed(self, row: Mapping[str, Any]) -> str:
        return str(row.get("application_no") or row["raw_document_id"])

    def _classify(self, row: Mapping[str, Any]) -> Any:
        return classify_patent_filing(row)

    def _event_date(self, row: Mapping[str, Any]) -> date:
        return _to_date(row.get("application_date") or row["published_at"])

    def _title(self, row: Mapping[str, Any]) -> str:
        return str(row.get("patent_title") or row.get("title") or "PATENT")

    def _evidence_text(self, row: Mapping[str, Any]) -> str:
        applicant = row.get("applicant_name")
        tech = row.get("tech_category")
        parts = [str(p) for p in (self._title(row), applicant, tech) if p]
        return " / ".join(dict.fromkeys(parts))

    def _metrics(self, row: Mapping[str, Any]) -> list[dict[str, Any]]:
        return [
            {
                "metric_name": "patent_filing_count",
                "metric_value": 1,
                "metric_unit": "count",
            }
        ]

    async def _enqueue_followups(
        self,
        *,
        stock_ids: list[int],
        raw_document_ids: list[int],
        as_of: str,
    ) -> list[int]:
        """Insert LLM enrichment before analysis: enqueue ENRICH_PATENT per stock.

        Each ENRICH_PATENT carries the just-normalized ``raw_document_ids`` and
        enqueues the per-stock ANALYZE_PATENT once enrichment is cached, so
        the patent score is built on the freshly extracted significance features.
        With nothing normalized there is nothing to enrich, so fall back to the
        plain analysis enqueue (keeps an empty run from stalling the pipeline).
        """
        if not raw_document_ids:
            return await _enqueue_analysis_for_stocks(
                queue_repository=self._queue_repository,
                stock_ids=stock_ids,
                as_of=as_of,
                task_type=ANALYZE_PATENT,
            )
        task_ids: list[int] = []
        for stock_id in dict.fromkeys(stock_ids):  # distinct, order-preserving
            task_id = await self._queue_repository.enqueue(
                stock_id=stock_id,
                task_type=ENRICH_PATENT,
                priority="batch",
                source_raw_ids=raw_document_ids,
                task_context={"as_of": as_of, "source_type": "PATENT"},
                dedupe=True,
            )
            task_ids.append(task_id)
        return task_ids


class DataLabNormalizeTaskHandler:
    """NORMALIZE_DATALAB — promotes DataLab raw to source_documents/signal_events.

    DataLab differs structurally from hiring/patent (so it does not subclass
    ``_AltNormalizeBase``):
      - raw lives in ``datalab_raw_documents`` (NOT ``raw_documents``), so it
        anchors via the generic external anchor ``external_ref_type='datalab_raw_documents'``
        + ``external_ref_id=datalab_raw_documents.id`` (migration 004).
      - one observation fans out to N stocks via ``datalab_category_stocks``, so
        each raw row produces one source_document + signal_event PER mapped stock.

    The task carries ``source_raw_ids`` = [datalab_raw_documents.id]; the loader
    resolves each row's ``category_id`` → mapped stocks. After normalization it
    enqueues one deduped ANALYZE_DATALAB per distinct stock (same shape as
    hiring/patent's per-source analysis), keeping the windowed analysis path intact.
    """

    SOURCE_TYPE = "DATALAB"
    SOURCE_NAME_DEFAULT = "NAVER_DATALAB"
    RELIABILITY_LEVEL = "medium"
    IS_OFFICIAL = False
    EXTERNAL_REF_TYPE = "datalab_raw_documents"

    def __init__(self, connection: Any) -> None:
        self._connection = connection
        self._raw_detail_repository = RawDetailRepository(connection)
        self._normalization_repository = NormalizationRepository(connection)
        self._queue_repository = ProcessingQueueRepository(connection)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        task_context = _task_context(task.get("task_context"))
        as_of = _as_of_str(task_context.get("as_of"))
        raw_document_ids = _source_raw_ids(task.get("source_raw_ids"))
        rows = await self._raw_detail_repository.list_datalab_details_by_raw_ids(raw_document_ids)

        signal_event_ids: list[int] = []
        stock_ids: list[int] = []
        normalized_count = 0
        # Cache category → mapped stocks: a task often carries many observations of
        # the same category (different dates), so resolve each mapping only once.
        category_stocks: dict[int, list[Any]] = {}
        for row in rows:
            raw_document_id = int(row["raw_document_id"])
            category_id = int(row["category_id"])
            # category → mapped stocks (fan-out). One observation may map to many.
            if category_id not in category_stocks:
                category_stocks[category_id] = (
                    await self._raw_detail_repository.list_stocks_for_datalab_category(category_id)
                )
            mapped = category_stocks[category_id]
            classification = classify_datalab_observation(row)
            for mapped_row in mapped:
                stock_id = int(mapped_row["stock_id"])
                source_document = (
                    await self._normalization_repository.upsert_external_source_document(
                        external_ref_type=self.EXTERNAL_REF_TYPE,
                        external_ref_id=raw_document_id,
                        stock_id=stock_id,
                        source_type=self.SOURCE_TYPE,
                        source_name=str(row.get("source_name") or self.SOURCE_NAME_DEFAULT),
                        title=self._title(row),
                        source_url=row.get("source_url"),
                        published_at=row["published_at"],
                        collected_at=row["collected_at"],
                        reliability_level=self.RELIABILITY_LEVEL,
                        is_official=self.IS_OFFICIAL,
                    )
                )
                signal_event = await self._normalization_repository.upsert_signal_event(
                    stock_id=stock_id,
                    source_document_id=source_document["id"],
                    # event_hash unique per (observation, stock) — the fan-out key.
                    event_hash=make_source_hash(
                        self.SOURCE_TYPE, f"{raw_document_id}|{stock_id}"
                    ),
                    source_type=self.SOURCE_TYPE,
                    event_type=classification.event_type,
                    event_date=_to_date(row["observed_date"]),
                    signal_direction=classification.signal_direction,
                    impact_level=classification.impact_level,
                    title=self._title(row),
                    summary=self._summary(row),
                    evidence_text=self._evidence_text(row),
                    evidence_url=row.get("source_url"),
                    needs_review=classification.needs_review,
                )
                signal_event_ids.append(int(signal_event["id"]))
                stock_ids.append(stock_id)
                for metric in self._metrics(row):
                    await self._normalization_repository.upsert_signal_metric(
                        signal_event_id=int(signal_event["id"]),
                        **metric,
                    )
                await self._normalization_repository.record_validation_log(
                    target_type="signal_event",
                    target_id_int=int(signal_event["id"]),
                    validation_type="source_trace",
                    passed=True,
                    message=(
                        f"Normalized from datalab_raw_documents.id={raw_document_id} "
                        f"category_id={category_id}"
                    ),
                )
                normalized_count += 1

        analysis_task_ids = await _enqueue_analysis_for_stocks(
            queue_repository=self._queue_repository,
            stock_ids=stock_ids,
            as_of=as_of,
            task_type=ANALYZE_DATALAB,
        )
        return {
            "normalized_count": normalized_count,
            "signal_event_ids": signal_event_ids,
            "analysis_task_ids": analysis_task_ids,
        }

    def _title(self, row: Mapping[str, Any]) -> str:
        return str(row.get("title") or row.get("keyword") or self.SOURCE_TYPE)

    def _summary(self, row: Mapping[str, Any]) -> str:
        keyword = row.get("keyword")
        observed = row.get("observed_date")
        return f"DataLab search trend for '{keyword}' on {observed}"

    def _evidence_text(self, row: Mapping[str, Any]) -> str:
        parts = [
            str(p)
            for p in (row.get("keyword"), row.get("keyword_group"), row.get("period_type"))
            if p
        ]
        return " / ".join(dict.fromkeys(parts)) or self.SOURCE_TYPE

    def _metrics(self, row: Mapping[str, Any]) -> list[dict[str, Any]]:
        search_index = row.get("search_index")
        if search_index is None:
            return []
        return [
            {
                "metric_name": "datalab_search_index",
                "metric_value": search_index,
                "metric_unit": "index",
                "previous_value": row.get("previous_search_index"),
                "change_pct": row.get("change_pct"),
            }
        ]


class PatentEnrichTaskHandler:
    """ENRICH_PATENT — LLM significance enrichment between normalize and analyze.

    Enriches only the patents named in ``source_raw_ids`` (the rows the matching
    NORMALIZE_PATENT task just promoted), caches the features in
    ``patent_raw_details.llm_features``, then enqueues the per-stock
    ANALYZE_PATENT so analysis runs on the freshly extracted significance.

    Best-effort by design: when no Gemini key is configured (or the client cannot
    be built) it skips the LLM step and still enqueues analysis, so the pipeline
    degrades to the count-based patent score instead of stalling. This is the
    enrichment boundary — NOT a collector (collectors never call an LLM) and NOT
    an analyzer (analyzers only read the cache).
    """

    def __init__(
        self,
        connection: Any,
        *,
        client: Any | None = None,
        client_factory: Callable[[], Any] | None = None,
        batch_limit: int = 200,
    ) -> None:
        self._connection = connection
        self._raw_detail_repository = RawDetailRepository(connection)
        self._queue_repository = ProcessingQueueRepository(connection)
        self._client = client
        self._client_factory = client_factory
        self._batch_limit = batch_limit

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        task_context = _task_context(task.get("task_context"))
        as_of = _as_of_str(task_context.get("as_of"))
        raw_document_ids = _source_raw_ids(task.get("source_raw_ids"))
        stock_ids = [int(task["stock_id"])] if task.get("stock_id") is not None else []

        client = self._resolve_client()
        stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
        if client is not None and raw_document_ids:
            enricher = PatentEnricher(
                self._raw_detail_repository,
                client,
                batch_limit=self._batch_limit,
                raw_document_ids=raw_document_ids,
            )
            stats = await enricher.run()

        analysis_task_ids = await _enqueue_analysis_for_stocks(
            queue_repository=self._queue_repository,
            stock_ids=stock_ids,
            as_of=as_of,
            task_type=ANALYZE_PATENT,
        )
        return {
            "enriched": stats,
            "llm_skipped": client is None,
            "analysis_task_ids": analysis_task_ids,
        }

    def _resolve_client(self) -> Any | None:
        """The injected client, else a lazily-built Gemini client, else None.

        Returns None (enrichment disabled, analysis still runs) when no key is
        configured or the client raises on construction, so a missing key never
        sinks the pipeline.
        """
        if self._client is not None:
            return self._client
        if self._client_factory is not None:
            return self._client_factory()
        if not get_settings().gemini_api_key:
            return None
        try:
            from app.clients.gemini_client import GeminiJsonClient

            return GeminiJsonClient(api_key=get_settings().gemini_api_key)
        except Exception:  # noqa: BLE001 — missing key/transport: degrade, don't stall
            return None


class HiringSkillEnrichTaskHandler:
    """ENRICH_HIRING — OCR skill enrichment between normalize and analyze.

    Enriches only the postings named in ``source_raw_ids`` (the rows the matching
    NORMALIZE_HIRING task just promoted): reads each poster image, OCRs it with
    Tesseract, caches the extracted skill set in ``hiring_raw_details.ocr_skills``,
    then enqueues the per-stock ANALYZE_HIRING so analysis runs on the fresh
    skills. Mirrors ``PatentEnrichTaskHandler``.

    Best-effort by design: when the OCR processor cannot be built (Pillow/
    pytesseract or the tesseract binary absent) it skips OCR and still enqueues
    analysis, so the pipeline degrades to the count-based hiring score instead of
    stalling. This is the enrichment boundary — NOT a collector and NOT an analyzer.
    """

    def __init__(
        self,
        connection: Any,
        *,
        ocr: Any | None = None,
        ocr_factory: Callable[[], Any] | None = None,
        batch_limit: int = 200,
    ) -> None:
        self._connection = connection
        self._raw_detail_repository = RawDetailRepository(connection)
        self._queue_repository = ProcessingQueueRepository(connection)
        self._ocr = ocr
        self._ocr_factory = ocr_factory
        self._batch_limit = batch_limit

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        task_context = _task_context(task.get("task_context"))
        as_of = _as_of_str(task_context.get("as_of"))
        raw_document_ids = _source_raw_ids(task.get("source_raw_ids"))
        stock_ids = [int(task["stock_id"])] if task.get("stock_id") is not None else []

        ocr = self._resolve_ocr()
        stats = {"total": 0, "success": 0, "failed": 0, "skipped": 0}
        if ocr is not None and raw_document_ids:
            enricher = HiringSkillEnricher(
                self._raw_detail_repository,
                ocr,
                batch_limit=self._batch_limit,
                raw_document_ids=raw_document_ids,
            )
            stats = await enricher.run()

        analysis_task_ids = await _enqueue_analysis_for_stocks(
            queue_repository=self._queue_repository,
            stock_ids=stock_ids,
            as_of=as_of,
            task_type=ANALYZE_HIRING,
        )
        return {
            "enriched": stats,
            "ocr_skipped": ocr is None,
            "analysis_task_ids": analysis_task_ids,
        }

    def _resolve_ocr(self) -> Any | None:
        """The injected OCR processor, else a lazily-built Tesseract one, else None.

        Returns None (enrichment disabled, analysis still runs) when the processor
        cannot be constructed, so missing OCR deps never sink the pipeline.
        """
        if self._ocr is not None:
            return self._ocr
        if self._ocr_factory is not None:
            return self._ocr_factory()
        try:
            from app.enrichment.tesseract_ocr import TesseractOCRProcessor

            return TesseractOCRProcessor()
        except Exception:  # noqa: BLE001 — missing deps: degrade, don't stall
            return None


class AlternativeAnalyzeTaskHandler:
    """Per-stock, per-source analysis — the queue-triggered AlternativeAgent.

    One handler instance is registered per ANALYZE_{SOURCE} stage (DATALAB/HIRING/
    PATENT) with a single-source ``registrations`` list, so each task analyzes ONE
    source (C안 Phase 3). The class stays source-agnostic — it iterates whatever
    registrations it is given (one, or the full trio for ad-hoc/CLI use). Each
    source is published as its OWN signal (build_source_signal → its own run_key),
    never merged cross-source. Runs its registration(s) **sequentially** on the
    handler's single asyncpg connection; batch throughput comes from running many
    ANALYZE_{SOURCE} tasks via the queue's run-batch loop.
    """

    def __init__(
        self,
        connection: Any,
        *,
        registrations: Sequence[SourceRegistration] | None = None,
        aggregator_config: AggregatorConfig | None = None,
        runtime_config: AnalyzerRuntimeConfig | None = None,
        repository_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._connection = connection
        self._registrations = list(registrations) if registrations is not None else build_registry()
        self._aggregator_config = aggregator_config or AggregatorConfig.from_env()
        self._runtime = runtime_config or AnalyzerRuntimeConfig.from_env()
        self._repository_factory = repository_factory or (lambda conn: RawDetailRepository(conn))

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        # LLM 코호트 채점이 이 소스의 점수를 소유하면 per-stock 결정론 분석은 skip —
        # 같은 (stock, date, run_key) 에 두 경로가 쓰면 최신행이 비결정적으로 이긴다(이중 인큐).
        from app.core.config import get_settings

        settings = get_settings()
        registrations = [
            r for r in self._registrations if not settings.llm_scoring_covers(r.source)
        ]
        if not registrations:
            return {
                "skipped": "llm_scoring",
                "stock_id": stock_id,
                "analyzed_count": 0,
                "available_sources": [],
                "sources": [],
            }
        task_context = _task_context(task.get("task_context"))
        as_of = _to_date(task_context.get("as_of")) if task_context.get("as_of") else date.today()
        stock_code = await self._resolve_stock_code(stock_id, task_context)
        # Orchestrator re-query hint (Wave-3): when this analyze was triggered by a
        # REQUERY_SOURCE round, ``requery_focus`` says *what/which axis* to re-check.
        # Threaded into the source agent's input ``context`` so a context-aware agent
        # can narrow its re-read; the rule-only path ignores context → byte-identical
        # to a normal analyze. Empty ({}) when this is a plain (non-re-query) analyze.
        analysis_context = _requery_context(task_context)

        repository = self._repository_factory(self._connection)
        persistence = AlternativeSignalPersistence(
            self._connection,
            registrations=self._registrations,
            runtime_config=self._runtime,
        )

        sources: list[dict[str, Any]] = []
        available_sources: list[str] = []
        for registration in registrations:
            result = await self._run_source(
                registration, repository, stock_id, stock_code, as_of, context=analysis_context
            )
            # Each source maps to its OWN single-source signal and final_signals
            # row (its own run_key) — a peer of DART, not a blended component.
            # Build + persist is wrapped per source: a signal-build or DB/persist
            # failure for one source must not sink the remaining sources (mirrors
            # the per-source isolation in _run_source). The failed source is still
            # recorded as data_status="failed" so the report shows "no data".
            try:
                signal = build_source_signal(result, self._aggregator_config)
                ids = await persistence.save(
                    stock_id=stock_id,
                    signal=signal,
                    analysis_date=as_of,
                    publish_final_signal=True,
                    run_key=registration.resolved_run_key,
                )
            except Exception as exc:  # noqa: BLE001 — isolate one source's failure
                logger.warning(
                    "Alternative source %s 발행 실패 (stock=%s): %s",
                    registration.source,
                    stock_code,
                    exc,
                )
                sources.append(
                    {
                        "source": result.source,
                        "run_key": registration.resolved_run_key,
                        "direction": "unknown",
                        "score": 0.0,
                        "data_status": "failed",
                        "analysis_result_id": None,
                        "final_signal_id": None,
                    }
                )
                continue
            if result.data_status != "failed":
                available_sources.append(result.source)
            sources.append(
                {
                    "source": result.source,
                    "run_key": registration.resolved_run_key,
                    "direction": signal.direction,
                    "score": signal.score,
                    "data_status": result.data_status,
                    "analysis_result_id": ids.get("analysis_result_id"),
                    "final_signal_id": ids.get("final_signal_id"),
                }
            )

        return {
            "analyzed_count": len(sources),
            "stock_id": stock_id,
            "available_sources": available_sources,
            "sources": sources,
        }

    async def _run_source(
        self,
        registration: SourceRegistration,
        repository: Any,
        stock_id: int,
        stock_code: str,
        as_of: date,
        *,
        context: dict[str, Any] | None = None,
    ) -> SourceResult:
        try:
            loader = registration.build_loader(repository)
            evidence = await loader.load(stock_id=stock_id, stock_code=stock_code, as_of=as_of)
            # Drive the analyzer through the SourceAgentInput/Output contract. By
            # default that is the transparent RuleSourceAgent adapter (pure field
            # mapping — scores/directions/flags unchanged vs. calling the analyzer
            # directly, see _from_output). A source may register an ``agent_factory``
            # (built from the connection) for a graph/LLM agent instead — DATALAB
            # uses it for the cause agent when DATALAB_LLM_ENABLED is on. Unset →
            # identical to the previous rule-only path.
            if registration.agent_factory is not None:
                agent = registration.agent_factory(self._connection)
            else:
                agent = RuleSourceAgent(registration.analyzer)
            output = await agent.analyze(
                SourceAgentInput(
                    source=registration.source,
                    stock_code=stock_code,
                    stock_id=stock_id,
                    analysis_date=as_of,
                    evidence=evidence,
                    # Additive: carries the re-query focus (if any) to agents that
                    # read it. Defaults to {} → identical to the pre-focus input.
                    context=dict(context) if context else {},
                )
            )
            return _from_output(output)
        except Exception as exc:  # one source failing must not sink the others
            # Observability: a source going to "failed" is either a genuine
            # loader/analyzer error OR a _from_output restore bug — _from_output
            # logs the latter with a stack trace before re-raising, so the two
            # are distinguishable in the logs (this warning fires for both).
            logger.warning(
                "Alternative source %s 분석 실패 (stock=%s): %s",
                registration.source,
                stock_code,
                exc,
            )
            return SourceResult(
                source=registration.source,
                stock_code=stock_code,
                direction="unknown",
                score=0.0,
                summary=f"{registration.source} 분석 실패: {exc}",
                risk_flags=["analyzer_error"],
                data_status="failed",
            )

    async def _resolve_stock_code(self, stock_id: int, task_context: dict[str, Any]) -> str:
        code = task_context.get("stock_code") or task_context.get("ticker")
        if code:
            return str(code).strip()
        fetched = await self._connection.fetchval(
            "SELECT ticker FROM stocks WHERE id = $1",
            stock_id,
        )
        return str(fetched).strip() if fetched else str(stock_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _instantiate_known(cls, data: Mapping[str, Any]):
    """Build a dataclass from a dict using only its declared fields.

    ``_to_output`` flattens dataclasses via ``asdict``, so the round-trip is
    lossless today. But rebuilding with ``cls(**data)`` would raise ``TypeError``
    the moment ``method_detail`` carries an unexpected key (e.g. a field renamed
    or added on one side of the contract). Filtering to known fields keeps the
    restore resilient to such schema drift while preserving every matching value.
    """
    allowed = {f.name for f in fields(cls)}
    return cls(**{k: v for k, v in data.items() if k in allowed})


def _from_output(output: SourceAgentOutput) -> SourceResult:
    """Convert a ``SourceAgentOutput`` back to the ``SourceResult`` the aggregator
    consumes — the exact inverse of ``RuleSourceAgent._to_output``.

    The adapter is a transparent field mapping: it carries ``score``, ``direction``,
    ``summary``, ``risk_flags``, ``llm_model``, and ``data_status`` straight through,
    and flattens ``evidence_items`` / ``report_meta`` / the attention sub-dict into
    ``method_detail`` as plain dicts. Here we rebuild those from ``method_detail``
    so every analyzer-owned field of the resulting ``SourceResult`` is identical to
    what ``analyzer.analyze`` returned — keeping Alternative end-to-end scores
    invariant. Additively, the agent's own provenance (``analysis_source`` /
    ``prompt_ver`` / ``llm_error`` / ``needs_review``) is carried onto the
    ``SourceResult`` so persistence can record it (those fields are None on a
    direct analyzer result and never affect scoring).
    """
    detail = output.method_detail
    raw_items = detail.get("evidence_items") or []
    raw_meta = detail.get("report_meta")
    attention = detail.get("attention") or {}
    try:
        evidence_items = [_instantiate_known(EvidenceItem, item) for item in raw_items]
        report_meta = _instantiate_known(ReportMeta, raw_meta) if raw_meta is not None else None
    except Exception:
        # A restore failure here is a wiring/schema bug, not a genuine analyzer
        # failure — log it loudly so it is not silently laundered into
        # data_status="failed" by the caller's broad except.
        logger.exception(
            "RuleSourceAgent output 복원 실패 (source=%s, stock=%s)",
            output.source,
            output.stock_code,
        )
        raise
    return SourceResult(
        source=output.source,
        stock_code=output.stock_code,
        direction=output.direction,
        score=output.score,
        summary=output.summary,
        evidence_items=evidence_items,
        risk_flags=list(output.risk_flags),
        data_status=output.data_status,
        report_meta=report_meta,
        llm_model=output.llm_model,
        # Trend-only cause tag the DataLab graph agent stashed in method_detail
        # (None for every rule-only source — keeps non-DataLab end-to-end invariant).
        cause=detail.get("cause"),
        cause_rationale=detail.get("cause_rationale"),
        cause_source=detail.get("cause_source"),
        # Neutral attention-spike layer ``_to_output`` stashed as a sub-dict —
        # restore all five so the per-source "주의 근거" routing and persistence
        # see them (empty dict for non-DataLab sources keeps everything None).
        attention_tier=attention.get("attention_tier"),
        attention_z=attention.get("attention_z"),
        attention_note=attention.get("attention_note"),
        expected_fwd_vol_mult=attention.get("expected_fwd_vol_mult"),
        expected_fwd_volume_mult=attention.get("expected_fwd_volume_mult"),
        # Agent provenance: without these a failed-LLM PATENT run persisted
        # indistinguishable from a successful one, and per-agent prompt versions
        # never reached agent_results.prompt_ver.
        analysis_source=output.analysis_source,
        prompt_ver=output.prompt_ver,
        llm_error=output.llm_error,
        needs_review=output.needs_review,
        # 표시 전용 구조화 데이터(_to_output 이 method_detail 에 넣어둔 것). 복원하지 않으면
        # persistence 가 보는 SourceResult 에서 사라져 score_breakdown 까지 못 간다.
        patent_meta=_restore_patent_meta(detail),
        hiring_meta=_restore_hiring_meta(detail),
    )


def _restore_patent_meta(detail: dict[str, Any]) -> PatentMeta | None:
    """method_detail.patent → PatentMeta. 없거나 모양이 다르면 None(점수 경로 무영향)."""
    block = detail.get("patent")
    if not isinstance(block, dict):
        return None
    recent = block.get("recent_publications")
    trend = block.get("filing_trend")
    return PatentMeta(
        recent_publications=list(recent) if isinstance(recent, list) else [],
        filing_trend=list(trend) if isinstance(trend, list) else [],
    )


def _restore_hiring_meta(detail: dict[str, Any]) -> HiringMeta | None:
    """method_detail.hiring → HiringMeta. 없거나 모양이 다르면 None(점수 경로 무영향)."""
    block = detail.get("hiring")
    if not isinstance(block, dict):
        return None
    postings = block.get("postings")
    return HiringMeta(postings=list(postings) if isinstance(postings, list) else [])


def analysis_task_context(as_of: str) -> dict[str, Any]:
    """Canonical per-source analysis (ANALYZE_{SOURCE}) dedupe key, shared by every producer.

    enqueue(dedupe=True) matches stock_id + task_type separately and compares
    task_context with IS NOT DISTINCT FROM, so the context must carry ONLY the
    stable as_of (YYYY-MM-DD) and nothing stock-specific (e.g. stock_code/ticker).
    Every producer (run_analyzers.py and _enqueue_analysis_for_stocks) builds its
    context here so the same stock+day+source always yields an identical key and
    dedupes to one task; diverging the shape between producers silently defeats
    the dedupe and piles up duplicate analysis tasks.
    """
    return {"as_of": as_of}


async def _enqueue_analysis_for_stocks(
    *,
    queue_repository: ProcessingQueueRepository,
    stock_ids: list[int],
    as_of: str,
    task_type: str,
) -> list[int]:
    """Enqueue one deduped per-source analysis (``task_type``) per distinct stock.

    ``task_type`` is the source's own ANALYZE_{SOURCE} stage (e.g. ANALYZE_HIRING)
    so each source routes to its own handler. The dedupe key stays as_of-only — the
    task_type already separates the sources, so the same stock+day yields one task
    per source.
    """
    task_ids: list[int] = []
    for stock_id in dict.fromkeys(stock_ids):  # distinct, order-preserving
        task_id = await queue_repository.enqueue(
            stock_id=stock_id,
            task_type=task_type,
            priority="batch",
            task_context=analysis_task_context(as_of),
            dedupe=True,
        )
        task_ids.append(task_id)
    return task_ids


def _task_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _requery_context(task_context: Mapping[str, Any]) -> dict[str, Any]:
    """Extract the orchestrator re-query hint into the source agent input context.

    Only the re-query keys (``requery_focus`` / ``requery_round``) are surfaced, and
    only when present — a plain analyze yields ``{}`` (== the default input context),
    so the non-re-query path is byte-identical. Additive by construction.
    """
    context: dict[str, Any] = {}
    if task_context.get("requery_focus") is not None:
        context["requery_focus"] = task_context["requery_focus"]
    if task_context.get("requery_round") is not None:
        context["requery_round"] = task_context["requery_round"]
    return context


def _source_raw_ids(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
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


def _as_of_str(value: Any = None) -> str:
    """Normalize any as_of input to a stable ``YYYY-MM-DD`` string.

    Defaults to today. Used for the ANALYZE_{SOURCE} dedupe key so the same
    stock on the same day always produces the identical task_context.
    """
    if value in (None, ""):
        return date.today().isoformat()
    return _to_date(value).isoformat()


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if len(text) == 8 and text.isdigit():
        return datetime.strptime(text, "%Y%m%d").date()
    return datetime.fromisoformat(text[:10]).date()
