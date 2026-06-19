"""Per-source signal builder — the replacement for the cross-source aggregator.

The Alternative harness no longer merges HIRING/PATENT/DATALAB into one blended
signal. Each analyzer's ``SourceResult`` becomes its OWN ``AlternativeSignal``
(published to ``final_signals`` under its own run_key), a peer of DART/REPORT
rather than pre-combined. So this module maps a single ``SourceResult`` onto the
``AlternativeSignal`` shape persistence already understands — no weighting, no
cross-source agreement, no missing-source blending.

Pure logic only: no LLM, no DB, no external calls. The confidence/evidence rules
are the single-source case of the former ``AlternativeAggregator`` (available=1,
total=1, no agreement bonus/penalty), so end-to-end per-source numbers are
unchanged from running the old aggregator on one source.
"""

from __future__ import annotations

import logging

from app.analyzers.config import AggregatorConfig
from app.schemas.alternative_signal import AlternativeSignal
from app.schemas.source_result import SourceResult

logger = logging.getLogger(__name__)

# SourceResult.score convention is signed [-1, +1] (see its docstring). A small
# tolerance absorbs float rounding so a legitimate ±1.0 is never rejected.
_SCORE_TOLERANCE = 1e-6

# docs/project-context.md §10: separate "긍정 근거" (facts pointing one way) from
# "주의 근거" (conflicts, sparse/stale samples). Plain strings a downstream
# judge/LLM can read as grounded evidence rather than a bare number.
_FLAG_CAUTIONS = {
    "low_base": "표본 부족",
    "insufficient_history": "관측 이력 부족",
    "stale_data": "데이터 정체(오래됨)",
    "search_spike": "검색 급등 구간(해석 주의)",
    "no_data": "데이터 없음",
    "risk_search": "위험 키워드 검색 급등(악재 신호)",
    "analyzer_error": "분석기 오류로 제외됨",
    "score_out_of_range": "점수 규약 위반으로 제외됨(스케일 불일치)",
}


def _score_in_range(score: float) -> bool:
    return -1.0 - _SCORE_TOLERANCE <= score <= 1.0 + _SCORE_TOLERANCE


def build_source_signal(
    result: SourceResult, config: AggregatorConfig | None = None
) -> AlternativeSignal:
    """Map one ``SourceResult`` onto a single-source ``AlternativeSignal``.

    A failed source (or one whose score violates the [-1, +1] contract) yields an
    ``unknown`` signal flagged for review — surfaced, not silently dropped.
    """
    config = config or AggregatorConfig.from_env()

    failed = result.data_status == "failed" or not _score_in_range(result.score)
    if not _score_in_range(result.score):
        logger.error(
            "build_source_signal: source %s score %.4f outside [-1, +1] — published "
            "as unknown (scale-convention violation; see SourceResult.score).",
            result.source,
            result.score,
        )

    risk_flags = _dedupe(
        list(result.risk_flags) + (["score_out_of_range"] if not _score_in_range(result.score) else [])
    )

    if failed:
        reason = _dedupe([_FLAG_CAUTIONS[f] for f in risk_flags if f in _FLAG_CAUTIONS]) or [
            f"{result.source} 데이터 누락"
        ]
        return AlternativeSignal(
            stock_code=result.stock_code,
            direction="unknown",
            score=0.0,
            confidence=0.0,
            source_agreement="MEDIUM",
            score_breakdown={},
            available_sources=[],
            missing_sources=[result.source],
            risk_flags=risk_flags,
            summary=result.summary or f"{result.source} 분석 신호 없음",
            per_source={result.source: result},
            consensus_score=0.0,
            positive_evidence=[],
            caution_evidence=reason,
        )

    score = round(result.score, 3)
    confidence = _confidence(result, config)
    positive_evidence, caution_evidence = _evidence(result)

    return AlternativeSignal(
        stock_code=result.stock_code,
        direction=result.direction,
        score=score,
        confidence=round(confidence, 3),
        source_agreement="MEDIUM",  # single source: no cross-source agreement
        score_breakdown={result.source: score},
        available_sources=[result.source],
        missing_sources=[],
        risk_flags=risk_flags,
        summary=result.summary or _summary(result, score),
        per_source={result.source: result},
        consensus_score=round(confidence * 100.0, 2),
        positive_evidence=positive_evidence,
        caution_evidence=caution_evidence,
    )


def _confidence(result: SourceResult, config: AggregatorConfig) -> float:
    """Single-source confidence: the former aggregator's formula at available=1,
    total=1 (coverage 1.0, no HIGH/LOW agreement bonus/penalty)."""
    confidence = config.confidence_base + config.confidence_per_source  # one source
    flags = set(result.risk_flags)
    if result.data_status == "partial":
        confidence *= config.partial_penalty
    if "stale_data" in flags:
        confidence *= config.stale_penalty
    if "low_base" in flags or "insufficient_history" in flags:
        confidence *= config.sparse_penalty
    return max(0.0, min(1.0, confidence))


def _evidence(result: SourceResult) -> tuple[list[str], list[str]]:
    positive: list[str] = []
    caution: list[str] = []

    head = ", ".join(it.title for it in result.evidence_items[:2]) or result.summary
    if result.direction == "positive":
        positive.append(f"{result.source}: {head}")
    elif result.direction == "negative":
        caution.append(f"{result.source}: {head}")
    for flag in result.risk_flags:
        if flag in _FLAG_CAUTIONS:
            caution.append(f"{result.source}: {_FLAG_CAUTIONS[flag]}")

    return _dedupe(positive), _dedupe(caution)


def _summary(result: SourceResult, score: float) -> str:
    return f"{result.stock_code} {result.source} 신호: 방향 {result.direction}, 점수 {score:+.3f}"


def _dedupe(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)
