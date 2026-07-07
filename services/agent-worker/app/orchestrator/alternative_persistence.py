"""Persist an AlternativeSignal to the analysis layer.

Writes only what the Agent role owns per ``table_responsibility.md``:
``analysis_results`` (1 row), ``agent_results`` (one per source, keyed by
``debate_method``), and ``final_signals`` (1 aggregated row). It does NOT write
``signal_events`` — those belong to a Normalizer, and DataLab's category-based
raw has no ``raw_documents`` row to anchor a ``source_documents`` FK. So
``source_signal_event_ids`` is left empty until a Normalizer exists.

Score scaling: analyzers/aggregator work in [-1, +1]; the DB columns are
0-100 CHECK-constrained, so every score passes through ``_to_100``.
"""

from __future__ import annotations

from typing import Any, Sequence

from app.analyzers.config import AnalyzerRuntimeConfig
from app.analyzers.registry import SourceRegistration, build_registry
from app.schemas.alternative_signal import AlternativeSignal
from app.schemas.source_result import Direction, SourceResult
from signal_alpha_data_access.repositories import AnalysisRepository


class AlternativeSignalPersistence:
    def __init__(
        self,
        connection: Any,
        *,
        registrations: Sequence[SourceRegistration] | None = None,
        runtime_config: AnalyzerRuntimeConfig | None = None,
    ) -> None:
        self._analysis = AnalysisRepository(connection)
        registrations = list(registrations) if registrations is not None else build_registry()
        self._debate_methods = {r.source: r.debate_method for r in registrations}
        self._config = runtime_config or AnalyzerRuntimeConfig.from_env()

    async def save(
        self,
        *,
        stock_id: int,
        signal: AlternativeSignal,
        analysis_date: Any,
        publish_final_signal: bool = True,
        run_key: str | None = None,
    ) -> dict[str, Any]:
        # Each source now publishes its OWN final_signals row, so the handler
        # passes a per-source run_key (HIRING/PATENT/DATALAB). Falls back to the
        # runtime default for any caller that still saves a single combined row.
        run_key = run_key or self._config.run_key
        warning = "; ".join(signal.risk_flags) or None
        analysis_result = await self._analysis.upsert_analysis_result(
            stock_id=stock_id,
            analysis_date=analysis_date,
            run_key=run_key,
            source_signal_event_ids=[],  # no Normalizer yet → no signal_events to link
            base_score=_to_100(signal.score),
            analysis_mode="full",
            warning=warning,
            version=self._config.version,
        )
        result_id = analysis_result["id"]

        agent_result_ids: list[int] = []
        for source, source_result in signal.per_source.items():
            debate_method = self._debate_methods.get(source)
            if debate_method is None:
                continue  # source not registered for persistence
            agent_result = await self._analysis.upsert_agent_result(
                result_id=result_id,
                stock_id=stock_id,
                debate_method=debate_method,
                method_score=_to_100(source_result.score),
                method_signal=_signal_value(source_result.direction),
                method_detail=_method_detail(source_result),
                llm_model=source_result.llm_model,
                # 에이전트가 자기 prompt_ver 를 가져오면 그것을 쓴다(hiring-focus-v1,
                # datalab-cause-v1 등) — 에이전트를 안 거친 결과만 런타임 버전으로 폴백.
                prompt_ver=source_result.prompt_ver or self._config.version,
            )
            agent_result_ids.append(agent_result["id"])

        final_signal_id: int | None = None
        if publish_final_signal:
            needs_review = signal.direction == "unknown" or bool(signal.missing_sources)
            final_signal = await self._analysis.upsert_final_signal(
                stock_id=stock_id,
                analysis_result_id=result_id,
                signal_date=analysis_date,
                run_key=run_key,
                version=self._config.version,
                final_score=_to_100(signal.score),
                confidence=round(max(0.0, min(1.0, signal.confidence)) * 100.0, 2),
                signal=_signal_value(signal.direction),
                source_agreement=signal.source_agreement,
                score_breakdown={
                    src: _to_100(score) for src, score in signal.score_breakdown.items()
                },
                summary=signal.summary,
                warning_level=_warning_level(signal),
                needs_review=needs_review,
                is_published=True,
                published_at=analysis_date,
                consensus_score=round(max(0.0, min(100.0, signal.consensus_score)), 2),
                positive_evidence=signal.positive_evidence,
                caution_evidence=signal.caution_evidence,
            )
            final_signal_id = final_signal["id"]

        return {
            "analysis_result_id": result_id,
            "agent_result_ids": agent_result_ids,
            "final_signal_id": final_signal_id,
        }


def _to_100(score: float) -> float:
    """Map a [-1, +1] signed score onto the DB's 0-100 range."""
    return round(max(0.0, min(100.0, (score + 1.0) * 50.0)), 2)


def _signal_value(direction: Direction) -> str:
    # final_signals.signal / agent_results.method_signal exclude 'unknown'.
    return "neutral" if direction == "unknown" else direction


def _warning_level(signal: AlternativeSignal) -> str:
    if signal.direction == "unknown":
        return "WARNING"
    if signal.missing_sources or "search_spike" in signal.risk_flags or any(
        flag.startswith("stale") for flag in signal.risk_flags
    ):
        return "CAUTION"
    return "NORMAL"


def _method_detail(source_result: SourceResult) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "source": source_result.source,
        "summary": source_result.summary,
        "score": round(source_result.score, 3),
        "direction": source_result.direction,
        "risk_flags": source_result.risk_flags,
        "data_status": source_result.data_status,
        "highlights": [item.title for item in source_result.evidence_items],
    }
    # Trend-only attention cause (DataLab cause agent). Persisted in agent_results
    # JSONB only when present — no new column, no migration; None for every other
    # source leaves the detail shape unchanged.
    if source_result.cause is not None:
        detail["cause"] = source_result.cause
        detail["cause_rationale"] = source_result.cause_rationale
        detail["cause_source"] = source_result.cause_source
    # PATENT 표시 전용 구조화 데이터(최근 공개 특허 + 장기 출원 추이). agent_results JSONB
    # 에만 실어 score_breakdown.PATENT 로 흐른다 — 새 컬럼/마이그 없음(cause·valuation 동형).
    if source_result.patent_meta is not None:
        detail["patent"] = {
            "recent_publications": source_result.patent_meta.recent_publications,
            "filing_trend": source_result.patent_meta.filing_trend,
        }
    # Agent provenance — DART/REPORT lanes already persist these in method_detail
    # (dart/tasks.py), so mirror them here. Written only when the round-trip
    # carried them, keeping legacy (analyzer-direct) detail shapes unchanged.
    if source_result.analysis_source is not None:
        detail["analysis_source"] = source_result.analysis_source
    if source_result.needs_review is not None:
        detail["needs_review"] = source_result.needs_review
    if source_result.llm_error:
        detail["llm_error"] = source_result.llm_error
    return detail
