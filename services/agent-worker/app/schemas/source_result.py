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
class PatentMeta:
    """PATENT 소스의 화면용 구조화 데이터(점수/방향에는 영향 없음, 표시 전용).

    특허는 출원 후 ~18개월 뒤 공개되므로 두 날짜 축을 함께 보여준다:
      - ``recent_publications``: **최근 공개된** 특허 목록(공개일 최신순, 표시용 상위 N건).
        각 원소 = {application_no, title, application_date, publication_date, tech_category}.
      - ``filing_trend``: **장기 출원 추이**(출원 연도별 건수). 각 원소 = {year, count}.
    persistence 가 agent_results.method_detail.patent 로 실어 score_breakdown.PATENT
    까지 흐른다(새 컬럼/마이그 없음). REPORT 의 valuation 선례와 동형.
    """
    recent_publications: list[dict] = field(default_factory=list)
    filing_trend: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class DataLabMeta:
    """DATALAB 소스의 화면용 구조화 데이터(점수/방향에는 영향 없음, 표시 전용).

    "어떤 키워드가 떴는지"를 원본 그대로 보여준다:
      - ``keywords``: 분석 창에 실제 쓰인 키워드 목록(스파이크 우선·최신 관측 기준 상위 N건).
        각 원소 = {keyword, keyword_group, polarity, observed_date, search_index,
        change_pct, spiked}. ``spiked`` 는 창 내 is_spike 관측이 하나라도 있으면 True.
    persistence 가 agent_results.method_detail.datalab 으로 실어 score_breakdown.DATALAB
    까지 흐른다(새 컬럼/마이그 없음). HIRING 의 hiring_meta 선례와 동형.
    """

    keywords: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class HiringMeta:
    """HIRING 소스의 화면용 구조화 데이터(점수/방향에는 영향 없음, 표시 전용).

    채용 공고는 게시 후 마감일까지만 유효하므로 두 날짜 축을 함께 보여준다:
      - ``postings``: 최근 공고 목록(게시일 최신순, 표시용 상위 N건). 각 원소 =
        {job_title, posting_date, closing_date, closing_date_display, is_always_open, source_url}.
        ``closing_date`` 는 수집기가 정규화한 ISO 날짜(없으면 None), ``is_always_open`` 은
        상시채용 여부(마감일이 없는 정상 상태 — "만료"로 표시하면 안 된다).
    persistence 가 agent_results.method_detail.hiring 으로 실어 score_breakdown.HIRING
    까지 흐른다(새 컬럼/마이그 없음). PATENT 의 patent_meta 선례와 동형.
    """

    postings: list[dict] = field(default_factory=list)


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
    # PATENT 표시 전용 구조화 데이터(최근 공개 특허 목록 + 장기 출원 추이). None for
    # every other source. score/direction 에는 영향 없음(표시 전용) — persistence 가
    # method_detail.patent 로 실어 score_breakdown.PATENT 까지 흐른다.
    patent_meta: "PatentMeta | None" = None
    # HIRING 표시 전용 구조화 데이터(최근 공고의 게시일·마감일). None for every other
    # source. score/direction 에는 영향 없음 — persistence 가 method_detail.hiring 으로
    # 실어 score_breakdown.HIRING 까지 흘린다(patent_meta 동형).
    hiring_meta: "HiringMeta | None" = None
    # DATALAB 표시 전용 구조화 데이터(분석에 쓰인 키워드 목록). None for every other
    # source. score/direction 에는 영향 없음 — persistence 가 method_detail.datalab 으로
    # 실어 score_breakdown.DATALAB 까지 흘린다(hiring_meta 동형).
    datalab_meta: "DataLabMeta | None" = None
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
    # LLM 코호트 채점 경로 전용(additive). ``llm_confidence`` 는 LLM 이 출력한
    # [0, 0.85] 자기신뢰 — 결정론 confidence(``build_source_signal._confidence``)와
    # **다른 축**이라 별도 필드로 분리한다(섞으면 스케일 사고). ``score_change_reason``
    # 은 직전 점수 대비 |Δ| > 0.3 일 때 LLM 이 적는 변경 사유(표류 감사 재료). 둘 다
    # method_detail 로만 흐르고 점수·방향에는 영향 없다. 다른 모든 경로에서 None.
    llm_confidence: float | None = None
    score_change_reason: str | None = None
