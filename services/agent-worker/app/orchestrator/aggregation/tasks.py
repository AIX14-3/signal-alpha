from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.orchestrator.queue.task_types import SYNTHESIZE
from signal_alpha_data_access.repositories import (
    AnalysisRepository,
    NormalizationRepository,
    ProcessingQueueRepository,
)


AGGREGATE_RUN_KEY = "AGGREGATED"
AGGREGATE_VERSION = "final-agg-v1"
# REPORT 는 deterministic valuation 근거 소스로 수용하지만 점수 산정에는 넣지 않는다.
SOURCE_ORDER = ("DART", "PRICE", "REPORT", "ALTERNATIVE")
SCORING_SOURCES = {"DART", "ALTERNATIVE"}
VALID_DIRECTIONS = {"positive", "negative", "neutral", "mixed"}
SOURCE_ALIASES = {
    "DART": "DART",
    "PRICE": "PRICE",
    "REPORT": "REPORT",
    "ALTERNATIVE": "ALTERNATIVE",
    "HIRING": "ALTERNATIVE",
    "PATENT": "ALTERNATIVE",
    "DATALAB": "ALTERNATIVE",
}


@dataclass(frozen=True)
class NormalizedSourceResult:
    source: str
    analysis_result_id: int
    agent_result_id: int
    direction: str
    score: float
    score_100: float
    data_status: str
    needs_review: bool
    risk_flags: list[str]
    summary: str | None
    source_signal_event_ids: list[int]
    valuation: dict[str, Any] | None


class AggregateSignalTaskHandler:
    def __init__(self, connection: Any) -> None:
        self._analysis_repository = AnalysisRepository(connection)
        self._normalization_repository = NormalizationRepository(connection)
        self._queue_repository = ProcessingQueueRepository(connection)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        task_context = _task_context(task.get("task_context"))
        source_analysis_result_ids = _int_list(task.get("source_analysis_result_ids"))
        if not source_analysis_result_ids:
            return {
                "analysis_result_id": None,
                "final_signal_id": None,
                "aggregated_count": 0,
                "skipped_reason": "source_analysis_result_ids_required",
            }

        rows = [
            dict(row)
            for row in await self._analysis_repository.list_agent_results_for_aggregation(
                source_analysis_result_ids
            )
        ]
        normalized: list[NormalizedSourceResult] = []
        unknown_agent_result_ids: list[int] = []
        for row in rows:
            source_result = _normalize_source_result(row)
            if source_result is None:
                unknown_agent_result_ids.append(int(row["agent_result_id"]))
                continue
            normalized.append(source_result)

        for agent_result_id in unknown_agent_result_ids:
            await self._normalization_repository.record_validation_log(
                target_type="agent_result",
                target_id_int=agent_result_id,
                validation_type="aggregation_source_contract",
                passed=False,
                message="agent_results.method_detail.source is missing or unsupported.",
            )

        signal_date = _signal_date(rows, task_context)
        aggregate = _aggregate(normalized)
        source_signal_event_ids = _source_signal_event_ids(normalized)
        warning = "; ".join(aggregate["risk_flags"]) or None
        analysis_result = await self._analysis_repository.upsert_analysis_result(
            stock_id=stock_id,
            analysis_date=signal_date,
            run_key=AGGREGATE_RUN_KEY,
            source_signal_event_ids=source_signal_event_ids,
            base_score=aggregate["final_score"],
            analysis_mode="full",
            warning=warning,
            version=AGGREGATE_VERSION,
        )
        final_signal = await self._analysis_repository.upsert_final_signal(
            stock_id=stock_id,
            analysis_result_id=int(analysis_result["id"]),
            signal_date=signal_date,
            run_key=AGGREGATE_RUN_KEY,
            version=AGGREGATE_VERSION,
            final_score=aggregate["final_score"],
            confidence=aggregate["consensus_score"],
            signal=aggregate["signal"],
            source_agreement=aggregate["source_agreement"],
            warning_level=aggregate["warning_level"],
            score_breakdown=aggregate["score_breakdown"],
            summary=aggregate["summary"],
            bull_point=aggregate["bull_point"],
            bear_point=aggregate["bear_point"],
            needs_review=aggregate["needs_review"],
            is_published=aggregate["is_published"],
            published_at=datetime.combine(signal_date, datetime.min.time()) if aggregate["is_published"] else None,
            consensus_score=aggregate["consensus_score"],
            positive_evidence=aggregate["positive_evidence"],
            caution_evidence=aggregate["caution_evidence"],
        )
        priority = str(task_context.get("priority") or "batch")
        stock_code = task_context.get("stock_code")

        # 선형 체인(게이트2 = 신호·모델 품질): ML_INFER→META_COMBINE이 앞단에서 끝나고
        # 이 게이트가 발행 판정을 내린다. 발행분만 끝단 LLM 종합(SYNTHESIZE)으로 보내고,
        # 리스크 veto는 종합 "뒤"에서 동작한다(SYNTHESIZE가 RISK_VETO를 인큐). 미발행/needs_review는
        # 종합으로 보내지 않는다(버릴 게 아니라 처음부터 발행 대상이 아님).
        synthesize_task_id: int | None = None
        if aggregate["is_published"] and source_signal_event_ids:
            synthesize_task_id = await self._queue_repository.enqueue(
                stock_id=stock_id,
                task_type=SYNTHESIZE,
                priority=priority,
                source_signal_event_ids=source_signal_event_ids,
                task_context={
                    "final_signal_id": int(final_signal["id"]),
                    "stock_code": stock_code,
                    "run_key": "ML",
                },
                dedupe=True,
            )

        return {
            "analysis_result_id": analysis_result["id"],
            "final_signal_id": final_signal["id"],
            "aggregated_count": len(normalized),
            "signal": aggregate["signal"],
            "final_score": aggregate["final_score"],
            "source_agreement": aggregate["source_agreement"],
            "consensus_score": aggregate["consensus_score"],
            "warning_level": aggregate["warning_level"],
            "needs_review": aggregate["needs_review"],
            "is_published": aggregate["is_published"],
            "synthesize_task_id": synthesize_task_id,
        }


def _normalize_source_result(row: dict[str, Any]) -> NormalizedSourceResult | None:
    detail = _method_detail(row.get("method_detail"))
    source = _source_from(row, detail)
    if source is None:
        return None
    direction = _direction(detail.get("direction") or row.get("method_signal"))
    score = _source_score(row, detail)
    data_status = str(detail.get("data_status") or "ok")
    needs_review = bool(detail.get("needs_review")) or data_status in {"partial", "failed"}
    risk_flags = _string_list(detail.get("risk_flags"))
    valuation = _valuation_summary(detail)
    risk_flags.extend(_valuation_risk_flags(valuation))
    if _valuation_needs_review(valuation):
        needs_review = True
        data_status = "partial" if data_status == "ok" else data_status
    if data_status == "failed" and "failed_source" not in risk_flags:
        risk_flags.append("failed_source")
    return NormalizedSourceResult(
        source=source,
        analysis_result_id=int(row["analysis_result_id"]),
        agent_result_id=int(row["agent_result_id"]),
        direction=direction,
        score=score,
        score_100=_to_100(score),
        data_status=data_status,
        needs_review=needs_review,
        risk_flags=risk_flags,
        summary=detail.get("summary") if isinstance(detail.get("summary"), str) else None,
        source_signal_event_ids=_int_list(
            row.get("agent_source_signal_event_ids") or row.get("analysis_source_signal_event_ids")
        ),
        valuation=valuation,
    )


def _aggregate(results: list[NormalizedSourceResult]) -> dict[str, Any]:
    available = [result for result in results if result.data_status != "failed"]
    scoring = [result for result in available if result.source in SCORING_SOURCES]
    failed = [result for result in results if result.data_status == "failed"]
    missing_sources = [source for source in SOURCE_ORDER if not any(result.source == source for result in results)]
    aggregate_score = round(sum(result.score for result in scoring) / len(scoring), 3) if scoring else 0.0
    signal = _resolve_signal(available, aggregate_score)
    agreement_rate = _agreement_rate(available)
    source_agreement = _source_agreement(available, agreement_rate)
    consensus_score = 50.0 if len(available) == 1 else round(agreement_rate * 100, 2)
    risk_flags = _aggregate_risk_flags(
        available=available,
        scoring=scoring,
        failed=failed,
        missing_sources=missing_sources,
        signal=signal,
    )
    warning_level = _warning_level(
        available=available,
        scoring=scoring,
        failed=failed,
        missing_sources=missing_sources,
        signal=signal,
    )
    needs_review = warning_level in {"CAUTION", "WARNING"} or signal == "mixed" or any(
        result.needs_review for result in results
    )
    is_published = warning_level != "WARNING"
    return {
        "signal": signal,
        "final_score": _to_100(aggregate_score),
        "source_agreement": source_agreement,
        "consensus_score": consensus_score,
        "warning_level": warning_level,
        "needs_review": needs_review,
        "is_published": is_published,
        "score_breakdown": _score_breakdown(results),
        "summary": _summary(signal, available, missing_sources, warning_level),
        "bull_point": _evidence_point(available, "positive"),
        "bear_point": _evidence_point(available, "negative"),
        "positive_evidence": _evidence_items(available, "positive"),
        "caution_evidence": _caution_items(available, failed, missing_sources),
        "risk_flags": risk_flags,
    }


def _resolve_signal(results: list[NormalizedSourceResult], aggregate_score: float) -> str:
    if results and all(result.direction == "neutral" for result in results):
        return "neutral"
    has_mixed = any(result.direction == "mixed" for result in results)
    has_positive = any(result.direction == "positive" for result in results)
    has_negative = any(result.direction == "negative" for result in results)
    if has_mixed or (has_positive and has_negative):
        return "mixed"
    if aggregate_score >= 0.2:
        return "positive"
    if aggregate_score <= -0.2:
        return "negative"
    return "neutral"


def _score_breakdown(results: list[NormalizedSourceResult]) -> dict[str, dict[str, Any]]:
    by_source = {result.source: result for result in results}
    breakdown: dict[str, dict[str, Any]] = {}
    for source in SOURCE_ORDER:
        result = by_source.get(source)
        if result is None:
            breakdown[source] = {
                "direction": "unknown",
                "score": None,
                "score_100": None,
                "data_status": "missing",
                "needs_review": True,
                "analysis_result_id": None,
                "agent_result_id": None,
                "risk_flags": ["missing_source"],
            }
            continue
        breakdown[source] = {
            "direction": result.direction,
            "score": result.score,
            "score_100": result.score_100,
            "data_status": result.data_status,
            "needs_review": result.needs_review,
            "analysis_result_id": result.analysis_result_id,
            "agent_result_id": result.agent_result_id,
            "risk_flags": result.risk_flags,
            **({"valuation": result.valuation} if result.valuation is not None else {}),
        }
    return breakdown


def _source_from(row: dict[str, Any], detail: dict[str, Any]) -> str | None:
    candidates = [
        detail.get("source"),
        detail.get("source_type"),
        row.get("analysis_run_key"),
        row.get("analysis_mode"),
    ]
    for candidate in candidates:
        source = _normalize_source(candidate)
        if source:
            return source
    return None


def _normalize_source(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if not text:
        return None
    for key, source in SOURCE_ALIASES.items():
        if text == key or text.startswith(f"{key}_"):
            return source
    return None


def _source_score(row: dict[str, Any], detail: dict[str, Any]) -> float:
    for key in ("source_score", "score"):
        if detail.get(key) is not None:
            return _clamp_signed(_number(detail[key]))
    return _clamp_signed((_number(row.get("method_score")) / 50.0) - 1.0)


def _direction(value: Any) -> str:
    text = str(value or "neutral").strip().lower()
    return text if text in VALID_DIRECTIONS else "neutral"


def _warning_level(
    *,
    available: list[NormalizedSourceResult],
    scoring: list[NormalizedSourceResult],
    failed: list[NormalizedSourceResult],
    missing_sources: list[str],
    signal: str,
) -> str:
    if not available or not scoring:
        return "WARNING"
    if len(failed) >= 2 and len(available) <= 1:
        return "WARNING"
    if len(available) == 1 or len(missing_sources) >= 2 or signal == "mixed":
        return "CAUTION"
    if any(result.needs_review or result.data_status == "partial" for result in available):
        return "CAUTION"
    return "NORMAL"


def _aggregate_risk_flags(
    *,
    available: list[NormalizedSourceResult],
    scoring: list[NormalizedSourceResult],
    failed: list[NormalizedSourceResult],
    missing_sources: list[str],
    signal: str,
) -> list[str]:
    flags: list[str] = []
    if not available:
        flags.append("no_available_source")
    if not scoring:
        flags.append("no_scoring_source")
    if missing_sources:
        flags.append("missing_source")
    if failed:
        flags.append("failed_source")
    if signal == "mixed":
        flags.append("source_disagreement")
    for result in available:
        flags.extend(result.risk_flags)
    return _dedupe(flags)


def _agreement_rate(results: list[NormalizedSourceResult]) -> float:
    if not results:
        return 0.0
    directions = [result.direction for result in results if result.direction != "unknown"]
    if not directions:
        return 0.0
    return max(Counter(directions).values()) / len(results)


def _source_agreement(results: list[NormalizedSourceResult], agreement_rate: float) -> str:
    if len(results) <= 1:
        return "LOW"
    if agreement_rate >= 0.75:
        return "HIGH"
    if agreement_rate >= 0.5:
        return "MEDIUM"
    return "LOW"


def _summary(
    signal: str,
    available: list[NormalizedSourceResult],
    missing_sources: list[str],
    warning_level: str,
) -> str:
    if not available:
        return "Source data was not sufficient to publish a data direction."
    source_names = ", ".join(result.source for result in available)
    missing_text = f" Missing sources: {', '.join(missing_sources)}." if missing_sources else ""
    review_text = " Additional review is needed." if warning_level in {"CAUTION", "WARNING"} else ""
    return f"{source_names} data shows a {signal} data direction.{missing_text}{review_text}"


def _evidence_point(results: list[NormalizedSourceResult], direction: str) -> str | None:
    for result in results:
        if result.direction == direction and result.summary:
            return result.summary
    return None


def _evidence_items(results: list[NormalizedSourceResult], direction: str) -> list[dict[str, Any]]:
    return [
        {
            "source": result.source,
            "summary": result.summary,
            "agent_result_id": result.agent_result_id,
            "source_signal_event_ids": result.source_signal_event_ids,
        }
        for result in results
        if result.direction == direction
    ]


def _caution_items(
    available: list[NormalizedSourceResult],
    failed: list[NormalizedSourceResult],
    missing_sources: list[str],
) -> list[dict[str, Any]]:
    items = [
        {
            "source": result.source,
            "summary": result.summary,
            "risk_flags": result.risk_flags,
            "agent_result_id": result.agent_result_id,
            **({"valuation": result.valuation} if result.valuation is not None else {}),
        }
        for result in [*available, *failed]
        if result.needs_review or result.risk_flags or result.direction in {"negative", "mixed"}
    ]
    items.extend({"source": source, "risk_flags": ["missing_source"]} for source in missing_sources)
    return items


def _source_signal_event_ids(results: list[NormalizedSourceResult]) -> list[int]:
    ids: list[int] = []
    for result in results:
        ids.extend(result.source_signal_event_ids)
    return sorted(set(ids))


def _signal_date(rows: list[dict[str, Any]], task_context: dict[str, Any]) -> date:
    value = task_context.get("signal_date") or task_context.get("analysis_date")
    if value:
        return _to_date(value)
    dates = [_to_date(row["analysis_date"]) for row in rows if row.get("analysis_date")]
    return max(dates) if dates else date.today()


def _task_context(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        return json.loads(value)
    return dict(value)


def _method_detail(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return dict(value)


def _valuation_summary(detail: dict[str, Any]) -> dict[str, Any] | None:
    report_quant = detail.get("report_quant")
    if not isinstance(report_quant, dict):
        return None
    valuation = report_quant.get("valuation")
    if not isinstance(valuation, dict):
        return None
    return dict(valuation)


def _valuation_needs_review(valuation: dict[str, Any] | None) -> bool:
    if valuation is None:
        return False
    return bool(valuation.get("needs_review")) or valuation.get("data_status") == "partial"


def _valuation_risk_flags(valuation: dict[str, Any] | None) -> list[str]:
    if valuation is None:
        return []
    flags = _string_list(valuation.get("risk_flags"))
    if flags:
        return flags
    return ["valuation_review_required"] if _valuation_needs_review(valuation) else []


def _int_list(value: Any) -> list[int]:
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
            return [int(item.strip()) for item in inner.split(",") if item.strip()]
        parsed = json.loads(text)
        return [int(item) for item in parsed]
    return [int(value)]


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _to_date(value: Any) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return datetime.fromisoformat(str(value)[:10]).date()


def _number(value: Any) -> float:
    if isinstance(value, Decimal):
        return float(value)
    return float(value or 0.0)


def _clamp_signed(value: float) -> float:
    return round(max(-1.0, min(1.0, value)), 3)


def _to_100(score: float) -> float:
    return round(max(0.0, min(100.0, (score + 1.0) * 50.0)), 2)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
