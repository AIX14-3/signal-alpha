"""AlternativeAggregator — the diagram's "통합기 (합치는 자)".

Pure merge logic only: no LLM, no DB, no external calls. Takes the per-source
``SourceResult`` list the analyze handler collected and produces one
``AlternativeSignal``. Works for any number of sources — only the sources
actually present (and not failed) are blended, with weights renormalised over
them, so registering a new source needs no change here.
"""

from __future__ import annotations

from app.analyzers.config import AggregatorConfig
from app.schemas.alternative_signal import AlternativeSignal
from app.schemas.source_result import Direction, SourceResult


class AlternativeAggregator:
    def __init__(self, config: AggregatorConfig | None = None) -> None:
        self._config = config or AggregatorConfig.from_env()

    def merge(self, results: list[SourceResult], *, stock_code: str | None = None) -> AlternativeSignal:
        if not results and stock_code is None:
            raise ValueError("merge requires at least one SourceResult or a stock_code.")
        resolved_stock = stock_code or results[0].stock_code

        available = [r for r in results if r.data_status != "failed"]
        missing = [r for r in results if r.data_status == "failed"]
        # Only sources that actually contributed appear in the breakdown; missing
        # sources are reported via ``missing_sources``, not as a misleading score.
        score_breakdown = {r.source: round(r.score, 3) for r in available}

        risk_flags = _dedupe(
            [flag for r in results for flag in r.risk_flags]
            + [f"missing_{r.source.lower()}" for r in missing]
        )

        if not available:
            return AlternativeSignal(
                stock_code=resolved_stock,
                direction="unknown",
                score=0.0,
                confidence=0.0,
                source_agreement="LOW",
                score_breakdown=score_breakdown,
                available_sources=[],
                missing_sources=[r.source for r in missing],
                risk_flags=risk_flags,
                summary="가용 소스 신호가 없어 통합 신호를 산출할 수 없습니다.",
                per_source={r.source: r for r in results},
                consensus_score=0.0,
                positive_evidence=[],
                caution_evidence=[f"{r.source} 데이터 누락" for r in missing] or ["가용 소스 없음"],
            )

        score = self._weighted_score(available)
        direction = self._direction(score, available)
        agreement = self._agreement(available)
        confidence = self._confidence(available, total=len(results), agreement=agreement)
        positive_evidence, caution_evidence = self._evidence(available, missing)

        return AlternativeSignal(
            stock_code=resolved_stock,
            direction=direction,
            score=round(score, 3),
            confidence=round(confidence, 3),
            source_agreement=agreement,
            score_breakdown=score_breakdown,
            available_sources=[r.source for r in available],
            missing_sources=[r.source for r in missing],
            risk_flags=risk_flags,
            summary=self._summary(resolved_stock, direction, score, available, missing),
            per_source={r.source: r for r in results},
            consensus_score=round(confidence * 100.0, 2),
            positive_evidence=positive_evidence,
            caution_evidence=caution_evidence,
        )

    def _weighted_score(self, available: list[SourceResult]) -> float:
        weights = [self._config.weights.get(r.source, 0.0) for r in available]
        total = sum(weights)
        if total <= 0:  # no configured weights → equal blend
            weights = [1.0] * len(available)
            total = float(len(available))
        weighted = sum(r.score * w for r, w in zip(available, weights))
        return max(-1.0, min(1.0, weighted / total))

    def _direction(self, score: float, available: list[SourceResult]) -> Direction:
        directions = {r.direction for r in available}
        if "positive" in directions and "negative" in directions:
            return "mixed"
        if "mixed" in directions:
            return "mixed"
        if score >= self._config.positive_threshold:
            return "positive"
        if score <= self._config.negative_threshold:
            return "negative"
        return "neutral"

    def _agreement(self, available: list[SourceResult]) -> str:
        decided = [r.direction for r in available if r.direction not in ("unknown", "neutral")]
        if len(available) <= 1:
            return "MEDIUM"
        if "positive" in decided and "negative" in decided:
            return "LOW"
        if decided and len(set(decided)) == 1 and len(decided) == len(available):
            return "HIGH"
        return "MEDIUM"

    def _confidence(self, available: list[SourceResult], *, total: int, agreement: str) -> float:
        cfg = self._config
        confidence = cfg.confidence_base + cfg.confidence_per_source * len(available)
        # Coverage: a missing source lowers trust. Without this, the per-source
        # term clamps so 2-of-3 and 3-of-3 both read 1.0 — i.e. dropping a source
        # would not lower confidence. Coverage makes the omission visible.
        if total > 0:
            confidence *= len(available) / total
        # Data-quality penalties from per-source risk flags.
        flags = {flag for r in available for flag in r.risk_flags}
        if any(r.data_status == "partial" for r in available):
            confidence *= cfg.partial_penalty
        if "stale_data" in flags:
            confidence *= cfg.stale_penalty
        if "low_base" in flags or "insufficient_history" in flags:
            confidence *= cfg.sparse_penalty
        # Agreement: concordant sources raise trust, conflicting ones lower it.
        if agreement == "HIGH":
            confidence *= cfg.agreement_high_bonus
        elif agreement == "LOW":
            confidence *= cfg.agreement_low_penalty
        return max(0.0, min(1.0, confidence))

    # docs/project-context.md §10: separate "긍정 근거" (facts pointing one way) from
    # "주의 근거" (conflicts, sparse/stale samples, missing sources). Plain strings the
    # downstream judge/LLM can read as grounded evidence rather than a bare number.
    _FLAG_CAUTIONS = {
        "low_base": "표본 부족",
        "insufficient_history": "관측 이력 부족",
        "stale_data": "데이터 정체(오래됨)",
        "search_spike": "검색 급등 구간(해석 주의)",
        "no_data": "데이터 없음",
        "risk_search": "위험 키워드 검색 급등(악재 신호)",
        "analyzer_error": "분석기 오류로 제외됨",
    }

    def _evidence(
        self, available: list[SourceResult], missing: list[SourceResult]
    ) -> tuple[list[str], list[str]]:
        positive: list[str] = []
        caution: list[str] = []

        directions = {r.direction for r in available}
        conflicting = "positive" in directions and "negative" in directions

        for r in available:
            head = ", ".join(it.title for it in r.evidence_items[:2]) or r.summary
            if r.direction == "positive":
                positive.append(f"{r.source}: {head}")
            elif r.direction == "negative":
                # A negative source is a real bearish fact → caution side.
                caution.append(f"{r.source}: {head}")
            for flag in r.risk_flags:
                if flag in self._FLAG_CAUTIONS:
                    caution.append(f"{r.source}: {self._FLAG_CAUTIONS[flag]}")

        if conflicting:
            caution.append("소스 간 방향이 엇갈립니다(추가 확인 필요).")
        for r in missing:
            # 실패=missing 소스의 플래그(예: analyzer_error)도 표면화한다. flag-specific
            # 메시지가 있으면 그것을, 없으면 일반 누락 안내를 caution 으로 올린다.
            flag_notes = [self._FLAG_CAUTIONS[f] for f in r.risk_flags if f in self._FLAG_CAUTIONS]
            reason = "; ".join(flag_notes) if flag_notes else "통합 점수에 미반영"
            caution.append(f"{r.source} 데이터 누락 — {reason}")

        return _dedupe(positive), _dedupe(caution)

    def _summary(
        self,
        stock_code: str,
        direction: Direction,
        score: float,
        available: list[SourceResult],
        missing: list[SourceResult],
    ) -> str:
        parts = ", ".join(f"{r.source} {r.score:+.2f}" for r in available)
        text = (
            f"{stock_code} 대체데이터 통합 신호: 방향 {direction}, 점수 {score:+.3f} "
            f"(가용 {len(available)}개 소스 가중통합 — {parts})"
        )
        if missing:
            text += f". 누락 소스: {', '.join(r.source for r in missing)}"
        return text


def _dedupe(items: list[str]) -> list[str]:
    seen: dict[str, None] = {}
    for item in items:
        seen.setdefault(item, None)
    return list(seen)
