"""The combined output of the Alternative aggregation harness.

``AlternativeSignal`` is what ``AlternativeAggregator.merge`` produces from the
per-source ``SourceResult`` list — the diagram's "AlternativeSignal" box. It is
a pure data holder; persistence maps it onto ``final_signals``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.schemas.source_result import Direction, SourceResult

Agreement = str  # "HIGH" | "MEDIUM" | "LOW"


@dataclass(frozen=True)
class AlternativeSignal:
    stock_code: str
    direction: Direction
    score: float  # weighted, clamped to [-1.0, +1.0]
    confidence: float  # [0.0, 1.0] — internal; surfaced as consensus_score (see docs §10)
    source_agreement: Agreement
    score_breakdown: dict[str, float]  # source -> raw score [-1, +1]
    available_sources: list[str]
    missing_sources: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    summary: str = ""
    per_source: dict[str, SourceResult] = field(default_factory=dict)
    # docs/project-context.md §10: separate 긍정/주의 근거, avoid the word "confidence".
    consensus_score: float = 0.0  # [0..100] alignment/coverage strength (NOT investment confidence)
    positive_evidence: list[str] = field(default_factory=list)
    caution_evidence: list[str] = field(default_factory=list)

    @property
    def alignment_rate(self) -> Agreement:
        """docs §10 output name for source agreement (HIGH/MEDIUM/LOW).

        Always equals ``source_agreement`` — exposed as a derived alias so the
        dashboard keeps the "alignment_rate" label without a duplicate stored
        column. Single source of truth: ``source_agreement``.
        """
        return self.source_agreement
