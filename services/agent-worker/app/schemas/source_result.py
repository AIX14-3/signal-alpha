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
    source: SourceType
    stock_code: str
    direction: Direction
    score: float
    summary: str
    evidence_items: list[EvidenceItem] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    data_status: Literal["ok", "partial", "failed"] = "ok"
    report_meta: ReportMeta | None = None
