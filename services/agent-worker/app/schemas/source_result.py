from dataclasses import dataclass, field
from typing import Literal

from app.schemas.evidence import SourceType

Direction = Literal["positive", "neutral", "negative", "mixed", "unknown"]


@dataclass(frozen=True)
class EvidenceItem:
    title: str
    summary: str
    url: str | None = None
    published_at: str | None = None
    source_name: str | None = None


@dataclass(frozen=True)
class ReportMeta:
    """Report RAG 전용 집계 메타데이터"""
    avg_target: float | None
    upside_pct: float | None
    target_trend: Literal["up", "down", "flat", "unknown"]
    conflict_detected: bool
    opinions: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class SourceResult:
    """Per-source analysis result ``build_source_signal`` consumes.

    score convention: signed ``[-1.0, +1.0]`` (negative = bearish, 0 = neutral,
    positive = bullish). ``build_source_signal`` maps this raw score onto the
    source's own signal, and the persistence layer scales it to the DB's 0-100
    columns via ``_to_100`` only at the write boundary. Feeding a different scale
    (e.g. DART's native 0-100) is rejected: ``build_source_signal`` demotes any
    score outside ``[-1, +1]`` to an ``unknown`` signal rather than publishing it.
    """

    source: SourceType
    stock_code: str
    direction: Direction
    score: float  # [-1, +1] convention — see class docstring
    summary: str
    evidence_items: list[EvidenceItem] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    data_status: Literal["ok", "partial", "failed"] = "ok"
    report_meta: ReportMeta | None = None
    # LLM provenance: model name when an LLM contributed to this source's result
    # (e.g. DataLab polarity classification). None for pure-rule output. Flows to
    # agent_results.llm_model in persistence.
    llm_model: str | None = None
