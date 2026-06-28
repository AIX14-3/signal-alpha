from __future__ import annotations

import json
import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from app.orchestrator.queue.task_types import SYNTHESIZE
from signal_alpha_data_access.repositories import (
    AnalysisRepository,
    MetaSignalRepository,
    NormalizationRepository,
    ProcessingQueueRepository,
)


AGGREGATE_RUN_KEY = "AGGREGATED"
AGGREGATE_VERSION = "final-agg-v1"
# 통합 SRC 예측(메타러너 return 채널)의 meta_signals run_key. RETURN_COMBINE 이 적재한다.
SRC_RUN_KEY = "SRC"
# 예측 수익률 → 0-100 'AI 예측 점수' 변환 기울기(tanh). +3% → ~64, 0% → 50, -2% → ~41 (승인 매핑).
_RETURN_SCORE_STEEPNESS = 9.6
# 대체데이터(HIRING/PATENT/DATALAB)는 서로 다른 신호라 묶지 않고 **각자 독립 소스**로 점수에 넣는다
# (ALTERNATIVE 로 collapse 안 함). PRICE/REPORT 는 근거 소스로 수용하되 점수 산정에는 넣지 않는다.
SOURCE_ORDER = ("DART", "PRICE", "REPORT", "HIRING", "PATENT", "DATALAB")
SCORING_SOURCES = {"DART", "HIRING", "PATENT", "DATALAB"}
VALID_DIRECTIONS = {"positive", "negative", "neutral", "mixed"}
SOURCE_ALIASES = {
    "DART": "DART",
    "PRICE": "PRICE",
    "REPORT": "REPORT",
    "HIRING": "HIRING",
    "PATENT": "PATENT",
    "DATALAB": "DATALAB",
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
    # un-aliased 소스(HIRING/PATENT/DATALAB/DART/PRICE/REPORT). 대체데이터 collapse 폐기 후
    # ``source`` 와 동일하다(각 소스가 독립 peer). 하위호환·테스트 호환 위해 필드 유지(기본 "").
    fine_source: str = ""


class AggregateSignalTaskHandler:
    def __init__(self, connection: Any) -> None:
        self._analysis_repository = AnalysisRepository(connection)
        self._normalization_repository = NormalizationRepository(connection)
        self._queue_repository = ProcessingQueueRepository(connection)
        self._meta_repository = MetaSignalRepository(connection)

    async def __call__(self, task: Mapping[str, Any]) -> dict[str, Any]:
        stock_id = int(task["stock_id"])
        task_context = _task_context(task.get("task_context"))
        source_analysis_result_ids = _int_list(task.get("source_analysis_result_ids"))
        if source_analysis_result_ids:
            # Legacy single-producer path: aggregate exactly the ids handed in.
            rows = [
                dict(row)
                for row in await self._analysis_repository.list_agent_results_for_aggregation(
                    source_analysis_result_ids
                )
            ]
        else:
            # Fan-in path: gather EVERY source's latest result for this stock/date,
            # so DART/PRICE/HIRING/PATENT/DATALAB/REPORT blend into one AGGREGATED
            # signal instead of whichever single source happened to trigger us.
            rows = [
                dict(row)
                for row in await self._analysis_repository.list_latest_source_results_for_stock(
                    stock_id=stock_id,
                    analysis_date=_signal_date([], task_context),
                )
            ]
        if not rows:
            return {
                "analysis_result_id": None,
                "final_signal_id": None,
                "aggregated_count": 0,
                "skipped_reason": "no_source_results",
            }
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
        # 소스별 독립 집계: 대체데이터(HIRING/PATENT/DATALAB)를 묶지 않고 각자 peer 로 점수화한다.
        # _coalesce_by_source 는 같은 소스의 다중 행(예: DART 다중 이벤트 run_key)만 1 peer 로 합친다.
        coarse = _coalesce_by_source(normalized)
        aggregate = _aggregate(coarse)
        source_signal_event_ids = _source_signal_event_ids(normalized)
        warning = "; ".join(aggregate["risk_flags"]) or None

        # 발행 헤드라인 = 통합 SRC 예측(메타러너 return 채널). RETURN_COMBINE 이 meta_signals
        # (run_key="SRC")에 적재한 통합 예측 수익률을 0-100 점수로 변환해 헤드라인(signal/final_score)
        # 으로 쓴다. 결정론 SCORING_SOURCES 블렌드(_aggregate 의 signal/final_score)는 더 이상
        # 헤드라인이 아니며 score_breakdown/warning 등 표시·경보 메타로만 남는다. SRC 가 아직 없으면
        # (아티팩트 전무 또는 RETURN_COMBINE 미완) 중립(50)으로 발행하고, 다음 드레인에 SRC 가
        # 채워지면 AGGREGATE 재실행이 헤드라인을 갱신한다(eventual consistency — meta_signals 는
        # is_current 게이트가 없어 항상 읽힌다).
        src_row = await self._meta_repository.latest_for_stock(stock_id=stock_id, run_key=SRC_RUN_KEY)
        headline_signal, headline_score = _src_headline(src_row, signal_date)

        analysis_result = await self._analysis_repository.upsert_analysis_result(
            stock_id=stock_id,
            analysis_date=signal_date,
            run_key=AGGREGATE_RUN_KEY,
            source_signal_event_ids=source_signal_event_ids,
            base_score=headline_score,
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
            final_score=headline_score,
            confidence=aggregate["consensus_score"],
            signal=headline_signal,
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

        # 발행은 무조건. 모든 집계 신호를 끝단 LLM 종합(SYNTHESIZE)으로 보내고, SYNTHESIZE 가
        # 법적 금지단어 필터만 거쳐 곧장 PUBLISH_SIGNALS 를 인큐한다(발행 차단 게이트 폐기).
        # 근거 이벤트가 없어도 7예측률 서술은 가능하므로 SYNTHESIZE 로 보낸다. 발행 우선순위는
        # 체인(SYNTHESIZE→PUBLISH) 끝까지 전파해 immediate 신호가 batch 로 강등되지 않게 한다.
        synthesize_task_id = await self._queue_repository.enqueue(
            stock_id=stock_id,
            task_type=SYNTHESIZE,
            priority=priority,
            source_signal_event_ids=source_signal_event_ids,
            task_context={
                "final_signal_id": int(final_signal["id"]),
                "stock_code": stock_code,
                "run_key": "ML",
                "priority": priority,
            },
            dedupe=True,
        )

        return {
            "analysis_result_id": analysis_result["id"],
            "final_signal_id": final_signal["id"],
            "aggregated_count": len(normalized),
            "signal": headline_signal,
            "final_score": headline_score,
            # 결정론 블렌드(표시·경보 메타) — 헤드라인이 아니라 참고용.
            "deterministic_signal": aggregate["signal"],
            "deterministic_score": aggregate["final_score"],
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
        fine_source=_fine_source_from(row, detail) or source,
    )


# Source families recognized when un-aliasing a run_key/method_detail.source into
# its individual collector (the opposite of SOURCE_ALIASES, which collapses the
# alternative trio into ALTERNATIVE).
_FINE_SOURCES = ("DART", "PRICE", "REPORT", "HIRING", "PATENT", "DATALAB")


def _fine_source_from(row: dict[str, Any], detail: dict[str, Any]) -> str | None:
    for candidate in (detail.get("source"), detail.get("source_type"), row.get("analysis_run_key")):
        if candidate is None:
            continue
        text = str(candidate).strip().upper()
        for known in _FINE_SOURCES:
            if text == known or text.startswith(f"{known}_"):
                return known
    return None


def _coalesce_by_source(results: list[NormalizedSourceResult]) -> list[NormalizedSourceResult]:
    """Reduce the fan-in to exactly one peer per coarse source for scoring.

    A coarse source can legitimately have several rows for one (stock, date): the
    alternative trio (HIRING/PATENT/DATALAB all map to ALTERNATIVE) and DART (one
    analysis_result per event run_key). Without this, ``_aggregate`` would weight a
    source by how many rows it happened to produce. Each group is equal-averaged
    into one peer so the cross-source balance is preserved; a single-row group is
    passed through unchanged (only re-tagged with its coarse source).
    """
    groups: dict[str, list[NormalizedSourceResult]] = {}
    order: list[str] = []
    for result in results:
        if result.source not in groups:
            groups[result.source] = []
            order.append(result.source)
        groups[result.source].append(result)
    coalesced: list[NormalizedSourceResult] = []
    for source in order:
        group = groups[source]
        coalesced.append(group[0] if len(group) == 1 else _blend_group(source, group))
    return coalesced


def _blend_group(source: str, group: list[NormalizedSourceResult]) -> NormalizedSourceResult:
    score = round(sum(r.score for r in group) / len(group), 3)
    risk_flags: list[str] = []
    event_ids: list[int] = []
    for r in group:
        risk_flags.extend(r.risk_flags)
        event_ids.extend(r.source_signal_event_ids)
    summaries = [r.summary for r in group if r.summary]
    return NormalizedSourceResult(
        source=source,
        analysis_result_id=group[0].analysis_result_id,
        agent_result_id=group[0].agent_result_id,
        direction=_resolve_signal(group, score),
        score=score,
        score_100=_to_100(score),
        data_status=_blend_status([r.data_status for r in group]),
        needs_review=any(r.needs_review for r in group),
        risk_flags=_dedupe(risk_flags),
        summary=" / ".join(summaries) if summaries else None,
        source_signal_event_ids=sorted(set(event_ids)),
        valuation=next((r.valuation for r in group if r.valuation is not None), None),
        fine_source=source,
    )


def _blend_status(statuses: list[str]) -> str:
    """Most-informative status wins: ok > partial > no_signal > failed."""
    for level in ("ok", "partial", "no_signal"):
        if level in statuses:
            return level
    return "failed"


def _aggregate(results: list[NormalizedSourceResult]) -> dict[str, Any]:
    available = [result for result in results if result.data_status != "failed"]
    # A "no_signal" source ran but carries no direction — it must not dilute the
    # blended score toward 0, so it is available (shown, counted for coverage) but
    # excluded from the numeric scoring average.
    scoring = [
        result
        for result in available
        if result.source in SCORING_SOURCES and result.data_status != "no_signal"
    ]
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
    # 발행은 무조건(7예측률 무조건 발행). warning_level/근거 유무로 발행을 막지 않는다 —
    # warning_level/needs_review 는 표시용 메타로만 남는다(발행 차단 게이트 폐기).
    is_published = True
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
            "summary": result.summary,
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


def _src_headline(src_row: Any, signal_date: date) -> tuple[str, float]:
    """통합 SRC 예측(meta_signals run_key='SRC')을 발행 헤드라인(signal, 0-100 score)으로 변환.

    SRC ``final_score`` 는 예측 '수익률'(작은 부호값)이라 tanh 로 0-100 'AI 예측 점수'에 매핑한다
    (50=중립, 상승↑/하락↓). 해당 ``signal_date`` 의 SRC 가 없으면(아직 미계산) 중립(neutral, 50.0)
    으로 둔다 — 다음 드레인에 SRC 가 채워지면 AGGREGATE 재실행이 갱신한다.
    """
    if not src_row:
        return "neutral", 50.0
    row = dict(src_row)
    asof = row.get("asof_date")
    final_score = row.get("final_score")
    if asof is None or final_score is None or _to_date(asof) != signal_date:
        return "neutral", 50.0
    return _src_signal(row.get("direction")), _return_to_score_100(_number(final_score))


def _src_signal(direction: Any) -> str:
    text = str(direction or "neutral").strip().lower()
    return text if text in {"positive", "negative", "neutral"} else "neutral"


def _return_to_score_100(return_value: float) -> float:
    """예측 수익률(부호값) → 0-100 점수. tanh 로 50 중심에 매핑(상승↑/하락↓), 극단값은 포화."""
    return round(50.0 + 50.0 * math.tanh(_RETURN_SCORE_STEEPNESS * return_value), 2)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result
