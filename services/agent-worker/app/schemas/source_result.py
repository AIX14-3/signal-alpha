from dataclasses import dataclass, field
from typing import Literal

from app.schemas.evidence import SourceType

Direction = Literal["positive", "neutral", "negative", "mixed", "unknown"]

# Trend-only attention cause (협업안 §4). Why a DATALAB search spike happened,
# inferred from search-vs-price timing (lead/lag) and confirmed by an LLM:
#   - catalyst : search led the price move (information ahead of price)
#   - fomo     : search chased a price move that already happened (crowd attention)
#   - price_led: search merely tracked/lagged price (no independent information)
# Set ONLY by the DataLab cause agent; every other source leaves it None. Not a
# buy/sell call — a trace tag (docs §9). Surfaced via agent_results.method_detail.
Cause = Literal["catalyst", "fomo", "price_led"]


@dataclass(frozen=True)
class EvidenceItem:
    title: str
    summary: str
    url: str | None = None
    published_at: str | None = None
    source_name: str | None = None


@dataclass(frozen=True)
class ReportMeta:
    """Report valuation aggregation metadata."""
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
    # "no_signal": the source ran and had data (or legitimately had none) but
    # produced no actionable signal — distinct from "failed" (loader/analyzer
    # error) and from a source that never ran (surfaced as "missing" only at the
    # cross-source aggregation/breakdown layer, never set here).
    data_status: Literal["ok", "partial", "failed", "no_signal"] = "ok"
    report_meta: ReportMeta | None = None
    # LLM provenance: model name when an LLM contributed to this source's result
    # (e.g. DataLab polarity classification). None for pure-rule output. Flows to
    # agent_results.llm_model in persistence.
    llm_model: str | None = None
    # Trend-only attention cause (DATALAB cause agent). None for every other source
    # and for the pure-rule DataLab path. ``cause_source`` records how it was
    # decided ("llm" | "rules_fallback"); all three flow to agent_results.method_detail.
    cause: Cause | None = None
    cause_rationale: str | None = None
    cause_source: str | None = None
    # Neutral attention-spike layer (DATALAB only). A search-volume magnitude flag,
    # NOT a direction/return signal — it never moves ``direction``/``score``.
    # ``attention_note`` is the grounded "주의 근거" the aggregator routes into
    # ``caution_evidence``; the rest are structured fields a future
    # persistence/FE surface can render. All None outside the DataLab spike path.
    attention_tier: str | None = None
    attention_z: float | None = None
    attention_note: str | None = None
    expected_fwd_vol_mult: float | None = None
    expected_fwd_volume_mult: float | None = None
    # Agent-lane provenance (round-tripped from ``SourceAgentOutput`` by the
    # orchestrator's ``_from_output``): how the result was produced ("rules" |
    # "llm" | "rules_fallback" | ...), the agent's own prompt version, the LLM
    # failure detail, and the agent's review flag. All None when the result was
    # built directly by an analyzer (pre-agent constructors stay valid) — the
    # persistence layer then falls back to its runtime defaults. Mirrors what the
    # DART/REPORT lanes already persist so a failed-LLM run is distinguishable.
    analysis_source: str | None = None
    prompt_ver: str | None = None
    llm_error: str | None = None
    needs_review: bool | None = None
