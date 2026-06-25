from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Protocol, runtime_checkable

from app.schemas.evidence import RawEvidence, SourceType
from app.schemas.source_result import Direction

SourceAgentStatus = Literal["ok", "partial", "failed", "no_signal"]


@dataclass(frozen=True)
class SourceAgentInput:
    source: SourceType
    stock_code: str
    stock_id: int | None = None
    analysis_date: date | None = None
    run_key: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[RawEvidence] = field(default_factory=list)
    context: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SourceAgentOutput:
    """One source analyzer's result — the contract every source agent emits.

    score convention: signed ``[-1.0, +1.0]`` (negative = bearish, 0 = neutral,
    positive = bullish). The analyzer/aggregator layer computes in this signed
    scale; conversion to the DB's 0-100 columns happens only at the persistence
    boundary via ``_to_100``. Do NOT emit a different scale (e.g. 0-100): the
    AlternativeAggregator weight-averages raw scores, so a mismatched unit
    silently corrupts the blended signal. A producer in another scale must
    rescale to ``[-1, +1]`` before returning.
    """

    source: SourceType
    stock_code: str
    direction: Direction
    score: float  # [-1, +1] convention — see class docstring
    summary: str
    risk_flags: list[str] = field(default_factory=list)
    method_detail: dict[str, Any] = field(default_factory=dict)
    needs_review: bool = False
    data_status: SourceAgentStatus = "ok"
    analysis_source: str = "rules"
    llm_model: str | None = None
    prompt_ver: str = "1.0"
    llm_error: str | None = None


@runtime_checkable
class SourceAnalysisAgent(Protocol):
    source: SourceType

    async def analyze(self, input_data: SourceAgentInput) -> SourceAgentOutput:
        """Analyze normalized source data and return a source-agent result."""
