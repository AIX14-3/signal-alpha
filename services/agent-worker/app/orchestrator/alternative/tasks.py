"""Queue task handlers for the Alternative sources (hiring / patent / datalab).

Converges the Alternative sources onto the same queue model DART uses
(``orchestrator/dart/tasks.py``):

  NORMALIZE_HIRING / NORMALIZE_PATENT
      raw detail rows -> source_documents + signal_events + signal_metrics
      -> enqueue ANALYZE_ALTERNATIVE (per stock, deduped by stock + as_of)
  NORMALIZE_DATALAB (router; no signal_events — DataLab raw lives in a separate
      datalab_raw_documents table with no source_documents FK anchor)
      category_id -> datalab_category_stocks -> enqueue ANALYZE_ALTERNATIVE
  ANALYZE_ALTERNATIVE (per stock, cross-source)
      reuses build_registry()/loaders/analyzers/AlternativeAggregator and persists
      via AlternativeSignalPersistence — identical analysis to run_analyzers.py,
      just queue-triggered.

The analysis granularity is per-stock (windowed, cross-source), NOT per-event:
the Alternative analyzers score aggregated windows and the aggregator merges
sources, so a single ANALYZE task covers all registered sources for one stock.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any, Callable, Sequence

from app.aggregator import AlternativeAggregator
from app.analyzers.config import AnalyzerRuntimeConfig
from app.analyzers.datalab.normalize_rules import classify_datalab_observation
from app.analyzers.hiring.normalize_rules import classify_hiring_posting
from app.analyzers.patent.normalize_rules import classify_patent_filing
from app.analyzers.registry import SourceRegistration, build_registry
from app.orchestrator.alternative_persistence import AlternativeSignalPersistence
from app.orchestrator.queue.task_types import ANALYZE_ALTERNATIVE
from app.schemas.source_result import SourceResult
from app.utils.hash_utils import make_source_hash
from signal_alpha_data_access.repositories import (
    NormalizationRepository,
    ProcessingQueueRepository,
    RawDetailRepository,
)


class _AltNormalizeBase:
    """Shared normalize flow for raw sources anchored in ``raw_documents``.

    Subclasses provide the source type, the raw-detail loader, and the
    per-document classification; the base handles source_document/signal_event
    upserts and the per-stock ANALYZE_ALTERNATIVE enqueue.
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

        analysis_task_ids = await _enqueue_analysis_for_stocks(
            queue_repository=self._queue_repository,
            stock_ids=stock_ids,
            as_of=as_of,
        )
        return {
            "normalized_count": len(rows),
            "signal_event_ids": signal_event_ids,
            "analysis_task_ids": analysis_task_ids,
        }


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
    enqueues one deduped ANALYZE_ALTERNATIVE per distinct stock (same as
    hiring/patent), keeping the windowed cross-source analysis path intact.
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


class AlternativeAnalyzeTaskHandler:
    """Per-stock, cross-source analysis — the queue-triggered AlternativeAgent.

    Reuses the registered loaders/analyzers and the AlternativeAggregator, but
    runs the sources **sequentially** on the handler's single asyncpg connection
    (one connection cannot serve concurrent queries). With ~3 sources per stock
    the lost concurrency is negligible; batch throughput comes from running many
    ANALYZE_ALTERNATIVE tasks via the queue's run-batch loop.
    """

    def __init__(
        self,
        connection: Any,
        *,
        registrations: Sequence[SourceRegistration] | None = None,
        aggregator: AlternativeAggregator | None = None,
        runtime_config: AnalyzerRuntimeConfig | None = None,
        repository_factory: Callable[[Any], Any] | None = None,
    ) -> None:
        self._connection = connection
        self._registrations = list(registrations) if registrations is not None else build_registry()
        self._aggregator = aggregator or AlternativeAggregator()
        self._runtime = runtime_config or AnalyzerRuntimeConfig.from_env()
        self._repository_factory = repository_factory or (lambda conn: RawDetailRepository(conn))

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        task_context = _task_context(task.get("task_context"))
        as_of = _to_date(task_context.get("as_of")) if task_context.get("as_of") else date.today()
        stock_code = await self._resolve_stock_code(stock_id, task_context)

        repository = self._repository_factory(self._connection)
        results: list[SourceResult] = []
        for registration in self._registrations:
            results.append(
                await self._run_source(registration, repository, stock_id, stock_code, as_of)
            )

        signal = self._aggregator.merge(results, stock_code=stock_code)
        persistence = AlternativeSignalPersistence(
            self._connection,
            registrations=self._registrations,
            runtime_config=self._runtime,
        )
        ids = await persistence.save(
            stock_id=stock_id,
            signal=signal,
            analysis_date=as_of,
            publish_final_signal=True,
        )
        return {
            "analyzed_count": len(results),
            "stock_id": stock_id,
            "direction": signal.direction,
            "score": signal.score,
            "available_sources": signal.available_sources,
            "analysis_result_id": ids.get("analysis_result_id"),
            "final_signal_id": ids.get("final_signal_id"),
        }

    async def _run_source(
        self,
        registration: SourceRegistration,
        repository: Any,
        stock_id: int,
        stock_code: str,
        as_of: date,
    ) -> SourceResult:
        try:
            loader = registration.build_loader(repository)
            evidence = await loader.load(stock_id=stock_id, stock_code=stock_code, as_of=as_of)
            return await registration.analyzer.analyze(stock_code, evidence)
        except Exception as exc:  # one source failing must not sink the others
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

async def _enqueue_analysis_for_stocks(
    *,
    queue_repository: ProcessingQueueRepository,
    stock_ids: list[int],
    as_of: str,
) -> list[int]:
    """Enqueue one deduped ANALYZE_ALTERNATIVE per distinct stock.

    dedupe=True compares task_context with IS NOT DISTINCT FROM, so the as_of
    MUST be a stable YYYY-MM-DD string (see _as_of_str) — otherwise same-stock,
    same-day tasks would not dedupe and pile up.
    """
    task_ids: list[int] = []
    for stock_id in dict.fromkeys(stock_ids):  # distinct, order-preserving
        task_id = await queue_repository.enqueue(
            stock_id=stock_id,
            task_type=ANALYZE_ALTERNATIVE,
            priority="batch",
            task_context={"as_of": as_of},
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

    Defaults to today. Used for the ANALYZE_ALTERNATIVE dedupe key so the same
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
