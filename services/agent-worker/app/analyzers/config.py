"""Env-driven configuration for the Patent/DataLab analyzers and the
Alternative aggregation harness.

No thresholds, weights, lookback windows, or score-mapping constants are baked
into the rule/aggregator code — they all live here and are overridable via
environment variables. Tests construct these dataclasses directly with explicit
values, so the analyzers stay pure and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from os import getenv


def _float(name: str, default: float) -> float:
    raw = getenv(name)
    return float(raw) if raw not in (None, "") else default


def _int(name: str, default: int) -> int:
    raw = getenv(name)
    return int(raw) if raw not in (None, "") else default


def _float_opt(name: str, default: float | None) -> float | None:
    """Like ``_float`` but preserves a ``None`` default (env unset → stays None)."""
    raw = getenv(name)
    return float(raw) if raw not in (None, "") else default


@dataclass(frozen=True)
class PatentRuleConfig:
    """Scoring parameters for the Patent analyzer."""

    # 특허는 출원 후 ~18개월(≈540일) 뒤에야 공개된다. 방금 공개되어 지금 처음
    # 보이는 특허도 application_date(출원일) 기준으로는 이미 ~1.5년 전이라, 365일
    # 창으로는 창 밖으로 떨어져 no_signal 이 된다. 아래 값은 그 공개 지연을 덮도록
    # 넓힌 **임시(stopgap)** 조치다 — 여전히 출원일 기준이라 recent/prior 버킷의
    # "최근성" 의미가 부정확하다. 정식 해법은 publication_date(공개일) 기준 전환이며
    # 그 PR에서 이 값들을 정상 창으로 되돌린다. (env 로 언제든 오버라이드 가능)
    lookback_days: int = 900  # stopgap: 540(공개지연) + ~1년 관측창
    min_count: int = 3
    momentum_threshold: float = 0.5  # retained for legacy reference
    momentum_scale: float = 0.5  # tanh knee: momentum of 50% → 0.76*weight
    new_category_scale: float = 0.3  # tanh knee: 30% new-category ratio → 0.76*weight
    stale_days: int = 720  # stopgap: 공개 지연 감안, 정식(공개일) 전환 시 축소
    momentum_weight: float = 0.5
    new_category_weight: float = 0.3
    activity_weight: float = 0.2
    activity_scale: float = 5.0  # tanh knee: ~5 filings → 0.76*weight (count-graded R&D)
    # LLM-significance component (C3). Adds a positive "quality of R&D output" signal
    # when patents have been enriched; contributes 0 (exact fallback) when none are.
    significance_weight: float = 0.4
    significance_scale: float = 0.5  # tanh knee: mean significance 0.5 → 0.76*weight
    significance_min_enriched: int = 1  # need this many enriched filings to apply
    positive_threshold: float = 0.2
    negative_threshold: float = -0.2

    @classmethod
    def from_env(cls) -> "PatentRuleConfig":
        return cls(
            lookback_days=_int("PATENT_LOOKBACK_DAYS", cls.lookback_days),
            min_count=_int("PATENT_MIN_COUNT", cls.min_count),
            momentum_threshold=_float("PATENT_MOMENTUM_THRESHOLD", cls.momentum_threshold),
            momentum_scale=_float("PATENT_MOMENTUM_SCALE", cls.momentum_scale),
            new_category_scale=_float("PATENT_NEW_CATEGORY_SCALE", cls.new_category_scale),
            stale_days=_int("PATENT_STALE_DAYS", cls.stale_days),
            momentum_weight=_float("PATENT_MOMENTUM_WEIGHT", cls.momentum_weight),
            new_category_weight=_float("PATENT_NEW_CATEGORY_WEIGHT", cls.new_category_weight),
            activity_weight=_float("PATENT_ACTIVITY_WEIGHT", cls.activity_weight),
            activity_scale=_float("PATENT_ACTIVITY_SCALE", cls.activity_scale),
            significance_weight=_float("PATENT_SIGNIFICANCE_WEIGHT", cls.significance_weight),
            significance_scale=_float("PATENT_SIGNIFICANCE_SCALE", cls.significance_scale),
            significance_min_enriched=_int(
                "PATENT_SIGNIFICANCE_MIN_ENRICHED", cls.significance_min_enriched
            ),
            positive_threshold=_float("PATENT_POSITIVE_THRESHOLD", cls.positive_threshold),
            negative_threshold=_float("PATENT_NEGATIVE_THRESHOLD", cls.negative_threshold),
        )


@dataclass(frozen=True)
class DataLabRuleConfig:
    """Scoring parameters for the DataLab (search-trend) analyzer."""

    lookback_days: int = 30
    min_observations: int = 5
    min_prior_observations: int = 5  # prior-window count below this suppresses momentum/change
    momentum_threshold: float = 0.10  # retained for the small-sample guard wording only
    momentum_scale: float = 0.30  # tanh knee: momentum of 30% → 0.76*weight
    change_scale: float = 30.0  # tanh knee: avg change of 30%p → 0.76*weight
    spike_threshold: float = 0.20  # retained for legacy reference
    spike_scale: float = 0.30  # tanh knee: 30% spike share → 0.76*spike_weight
    risk_scale: float = 0.30  # tanh knee for RISK-keyword momentum (rising risk = bearish)
    risk_weight: float = 0.6  # max negative contribution from rising risk searches
    stale_days: int = 14
    momentum_weight: float = 0.6
    spike_weight: float = 0.2
    change_weight: float = 0.2
    positive_threshold: float = 0.2
    negative_threshold: float = -0.2

    # --- attention_spike (neutral magnitude flag) -------------------------------
    # NEUTRAL "주목/주의" layer: search-volume rolling-z → expected future
    # volume/volatility magnitude. NOT a direction/return signal (the directional
    # search→price hypothesis was rejected). Ported from the research finding
    # search→magnitude IC +0.37; see docs/attention-spike-flag-design.md.
    attention_window_days: int = 180  # loader fetch window (calendar) for the series
    attention_window: int = 60  # trailing points used as the rolling-z baseline
    attention_min_history: int = 30  # min prior points before a z is computed
    attention_z_caution: float = 1.5  # 정상→주의 boundary
    attention_z_watch: float = 2.5  # 주의→주목 boundary
    attention_z_surge: float = 3.5  # 주목→급증 boundary
    # z→magnitude multiplier table. PROVISIONAL placeholders (None) — the evidence
    # text cites numbers only once these are populated by the daily re-calibration
    # follow-up (calibrate_attention_flag.py). None ⇒ qualitative neutral wording.
    attention_vol_mult_caution: float | None = None
    attention_vol_mult_watch: float | None = None
    attention_vol_mult_surge: float | None = None
    attention_volume_mult_caution: float | None = None
    attention_volume_mult_watch: float | None = None
    attention_volume_mult_surge: float | None = None

    @classmethod
    def from_env(cls) -> "DataLabRuleConfig":
        return cls(
            lookback_days=_int("DATALAB_LOOKBACK_DAYS", cls.lookback_days),
            min_observations=_int("DATALAB_MIN_OBSERVATIONS", cls.min_observations),
            min_prior_observations=_int("DATALAB_MIN_PRIOR_OBSERVATIONS", cls.min_prior_observations),
            momentum_threshold=_float("DATALAB_MOMENTUM_THRESHOLD", cls.momentum_threshold),
            momentum_scale=_float("DATALAB_MOMENTUM_SCALE", cls.momentum_scale),
            change_scale=_float("DATALAB_CHANGE_SCALE", cls.change_scale),
            spike_threshold=_float("DATALAB_SPIKE_THRESHOLD", cls.spike_threshold),
            spike_scale=_float("DATALAB_SPIKE_SCALE", cls.spike_scale),
            risk_scale=_float("DATALAB_RISK_SCALE", cls.risk_scale),
            risk_weight=_float("DATALAB_RISK_WEIGHT", cls.risk_weight),
            stale_days=_int("DATALAB_STALE_DAYS", cls.stale_days),
            momentum_weight=_float("DATALAB_MOMENTUM_WEIGHT", cls.momentum_weight),
            spike_weight=_float("DATALAB_SPIKE_WEIGHT", cls.spike_weight),
            change_weight=_float("DATALAB_CHANGE_WEIGHT", cls.change_weight),
            positive_threshold=_float("DATALAB_POSITIVE_THRESHOLD", cls.positive_threshold),
            negative_threshold=_float("DATALAB_NEGATIVE_THRESHOLD", cls.negative_threshold),
            attention_window_days=_int("DATALAB_ATTENTION_WINDOW_DAYS", cls.attention_window_days),
            attention_window=_int("DATALAB_ATTENTION_WINDOW", cls.attention_window),
            attention_min_history=_int("DATALAB_ATTENTION_MIN_HISTORY", cls.attention_min_history),
            attention_z_caution=_float("DATALAB_ATTENTION_Z_CAUTION", cls.attention_z_caution),
            attention_z_watch=_float("DATALAB_ATTENTION_Z_WATCH", cls.attention_z_watch),
            attention_z_surge=_float("DATALAB_ATTENTION_Z_SURGE", cls.attention_z_surge),
            attention_vol_mult_caution=_float_opt(
                "DATALAB_ATTENTION_VOL_MULT_CAUTION", cls.attention_vol_mult_caution
            ),
            attention_vol_mult_watch=_float_opt(
                "DATALAB_ATTENTION_VOL_MULT_WATCH", cls.attention_vol_mult_watch
            ),
            attention_vol_mult_surge=_float_opt(
                "DATALAB_ATTENTION_VOL_MULT_SURGE", cls.attention_vol_mult_surge
            ),
            attention_volume_mult_caution=_float_opt(
                "DATALAB_ATTENTION_VOLUME_MULT_CAUTION", cls.attention_volume_mult_caution
            ),
            attention_volume_mult_watch=_float_opt(
                "DATALAB_ATTENTION_VOLUME_MULT_WATCH", cls.attention_volume_mult_watch
            ),
            attention_volume_mult_surge=_float_opt(
                "DATALAB_ATTENTION_VOLUME_MULT_SURGE", cls.attention_volume_mult_surge
            ),
        )


@dataclass(frozen=True)
class HiringRuleConfig:
    """Scoring parameters for the Hiring (job-postings) analyzer."""

    lookback_days: int = 90
    min_observations: int = 3
    min_prior_observations: int = 5  # prior-window count below this suppresses momentum/change
    momentum_threshold: float = 0.10  # retained for the small-sample guard wording only
    momentum_scale: float = 0.30  # tanh knee: momentum of 30% → 0.76*weight
    change_scale: float = 30.0  # tanh knee: avg change of 30%p → 0.76*weight
    stale_days: int = 45
    momentum_weight: float = 0.6
    change_weight: float = 0.4
    # Sector job-function demand component (C4): peer-company demand momentum for the
    # functions this stock depends on. Contributes 0 (exact fallback) when the
    # hiring_job_function_stocks mapping is unseeded or has no peer data.
    sector_demand_weight: float = 0.3
    sector_demand_scale: float = 0.30  # tanh knee: 30% sector momentum → 0.76*weight
    # OCR skill-breadth component: distinct in-demand tech skills the company is
    # hiring for (from hiring_raw_details.ocr_skills, ENRICH_HIRING). One-sided
    # positive — concrete tech hiring breadth is a tech-investment signal; absence
    # is silence, never negative. Contributes 0 (exact pre-enrichment fallback)
    # when no posting in the window has been OCR-enriched. Mirrors the patent
    # significance component.
    skill_weight: float = 0.3
    skill_scale: float = 6.0  # tanh knee: 6 distinct skills → 0.76*weight
    skill_min_enriched: int = 1  # need this many enriched observations to apply
    positive_threshold: float = 0.2
    negative_threshold: float = -0.2

    @classmethod
    def from_env(cls) -> "HiringRuleConfig":
        return cls(
            lookback_days=_int("HIRING_LOOKBACK_DAYS", cls.lookback_days),
            min_observations=_int("HIRING_MIN_OBSERVATIONS", cls.min_observations),
            min_prior_observations=_int("HIRING_MIN_PRIOR_OBSERVATIONS", cls.min_prior_observations),
            momentum_threshold=_float("HIRING_MOMENTUM_THRESHOLD", cls.momentum_threshold),
            momentum_scale=_float("HIRING_MOMENTUM_SCALE", cls.momentum_scale),
            change_scale=_float("HIRING_CHANGE_SCALE", cls.change_scale),
            stale_days=_int("HIRING_STALE_DAYS", cls.stale_days),
            momentum_weight=_float("HIRING_MOMENTUM_WEIGHT", cls.momentum_weight),
            change_weight=_float("HIRING_CHANGE_WEIGHT", cls.change_weight),
            sector_demand_weight=_float("HIRING_SECTOR_DEMAND_WEIGHT", cls.sector_demand_weight),
            sector_demand_scale=_float("HIRING_SECTOR_DEMAND_SCALE", cls.sector_demand_scale),
            skill_weight=_float("HIRING_SKILL_WEIGHT", cls.skill_weight),
            skill_scale=_float("HIRING_SKILL_SCALE", cls.skill_scale),
            skill_min_enriched=_int("HIRING_SKILL_MIN_ENRICHED", cls.skill_min_enriched),
            positive_threshold=_float("HIRING_POSITIVE_THRESHOLD", cls.positive_threshold),
            negative_threshold=_float("HIRING_NEGATIVE_THRESHOLD", cls.negative_threshold),
        )


@dataclass(frozen=True)
class ReportRuleConfig:
    """Scoring parameters for the Report (broker-report valuation) analyzer.

    방향 신호는 애널리스트 투자의견(매수 편향)이 아니라 **목표주가**에서 뽑는다:
    - revision: 최신 목표주가 vs 직전 목표주가 상향/하향(편향 없는 순수 방향 신호, 주 신호).
    - upside: 목표주가 vs 현재가 괴리(유효하나 매수 편향이 남아, 낮은 가중 + 큰 scale 로 완충 —
      전형적 20~30% upside 만으로는 방향을 못 넘기고 극단값에서만 기여).
    """

    min_target_price: float = 1.0  # 목표가/현재가 <= 0 방어(0 나눗셈)
    revision_weight: float = 0.6
    revision_scale: float = 0.10  # tanh knee: 목표가 +10% 상향 → 0.76*weight
    upside_weight: float = 0.25
    upside_scale: float = 0.35  # 큰 scale: 25% upside → ~0.15(중립대), 매수 편향 완충
    positive_threshold: float = 0.2
    negative_threshold: float = -0.2

    @classmethod
    def from_env(cls) -> "ReportRuleConfig":
        return cls(
            min_target_price=_float("REPORT_MIN_TARGET_PRICE", cls.min_target_price),
            revision_weight=_float("REPORT_REVISION_WEIGHT", cls.revision_weight),
            revision_scale=_float("REPORT_REVISION_SCALE", cls.revision_scale),
            upside_weight=_float("REPORT_UPSIDE_WEIGHT", cls.upside_weight),
            upside_scale=_float("REPORT_UPSIDE_SCALE", cls.upside_scale),
            positive_threshold=_float("REPORT_POSITIVE_THRESHOLD", cls.positive_threshold),
            negative_threshold=_float("REPORT_NEGATIVE_THRESHOLD", cls.negative_threshold),
        )


@dataclass(frozen=True)
class DartRuleConfig:
    """Scoring parameters for the DART analyzer (title-polarity + insider net-flow).

    방향은 이벤트의 ``signal_direction``(행위형 공시 극성 + 내부자 shares_delta 부호)에서 오고,
    점수는 임팩트 가중 순극성 비율을 graded(tanh)로 매핑한다. 미검증 신호이므로 방향 정렬 표시용.
    """

    polarity_weight: float = 0.7  # 순극성이 만들 수 있는 최대 |score|
    polarity_scale: float = 0.5  # tanh knee: 순극성 비율 0.5 → 0.76*weight
    positive_threshold: float = 0.2
    negative_threshold: float = -0.2

    @classmethod
    def from_env(cls) -> "DartRuleConfig":
        return cls(
            polarity_weight=_float("DART_POLARITY_WEIGHT", cls.polarity_weight),
            polarity_scale=_float("DART_POLARITY_SCALE", cls.polarity_scale),
            positive_threshold=_float("DART_POSITIVE_THRESHOLD", cls.positive_threshold),
            negative_threshold=_float("DART_NEGATIVE_THRESHOLD", cls.negative_threshold),
        )


@dataclass(frozen=True)
class AggregatorConfig:
    """Weights and thresholds for merging source signals into one signal.

    ``weights`` maps a SourceType to its raw weight. Only sources actually
    present in a run are used, and the weights are renormalised over them — so a
    teammate adding HIRING only needs to add ``ALT_WEIGHT_HIRING`` here.
    """

    weights: dict[str, float]
    positive_threshold: float = 0.2
    negative_threshold: float = -0.2
    # confidence = (base + per_source * available_sources) * data-quality multipliers,
    # clamped to [0, 1]. The multipliers below temper confidence by data quality so a
    # downstream LLM is told how much to trust the score, not just the score.
    #
    # base/per_source are tuned so confidence is *earned*, not capped: even a full
    # 3-source agreement (0.15 + 0.20*3 = 0.75, ×1.1 HIGH-agreement ≈ 0.83) stays
    # below 1.0. Earlier values (0.3 + 0.35*3 = 1.35) saturated at 100% before any
    # penalty, so confidence could only be docked, never built — and a 100% reading
    # overclaims certainty on an informational signal (docs §10: confidence 회피).
    confidence_base: float = 0.15
    confidence_per_source: float = 0.20
    partial_penalty: float = 0.8  # any source data_status == "partial"
    stale_penalty: float = 0.85  # any source flagged stale_data
    sparse_penalty: float = 0.8  # any source flagged low_base / insufficient_history
    agreement_high_bonus: float = 1.1  # all sources agree on direction
    agreement_low_penalty: float = 0.7  # sources conflict (positive vs negative)

    @classmethod
    def from_env(cls) -> "AggregatorConfig":
        weights = {
            "HIRING": _float("ALT_WEIGHT_HIRING", 0.34),
            "PATENT": _float("ALT_WEIGHT_PATENT", 0.33),
            "DATALAB": _float("ALT_WEIGHT_DATALAB", 0.33),
        }
        # Optional sources a teammate may register later. Only picked up when an
        # explicit weight env var is set, so the default 3-source run is intact.
        for optional in ("DART", "REPORT", "PRICE"):
            raw = getenv(f"ALT_WEIGHT_{optional}")
            if raw not in (None, ""):
                weights[optional] = float(raw)
        return cls(
            weights=weights,
            positive_threshold=_float("ALT_POSITIVE_THRESHOLD", cls.positive_threshold),
            negative_threshold=_float("ALT_NEGATIVE_THRESHOLD", cls.negative_threshold),
            confidence_base=_float("ALT_CONFIDENCE_BASE", cls.confidence_base),
            confidence_per_source=_float("ALT_CONFIDENCE_PER_SOURCE", cls.confidence_per_source),
            partial_penalty=_float("ALT_PARTIAL_PENALTY", cls.partial_penalty),
            stale_penalty=_float("ALT_STALE_PENALTY", cls.stale_penalty),
            sparse_penalty=_float("ALT_SPARSE_PENALTY", cls.sparse_penalty),
            agreement_high_bonus=_float("ALT_AGREEMENT_HIGH_BONUS", cls.agreement_high_bonus),
            agreement_low_penalty=_float("ALT_AGREEMENT_LOW_PENALTY", cls.agreement_low_penalty),
        )


@dataclass(frozen=True)
class AnalyzerRuntimeConfig:
    """Batch-runner knobs (concurrency, versioning)."""

    version: str = "1.0"
    batch_concurrency: int = 8
    run_key: str = "BATCH"

    @classmethod
    def from_env(cls) -> "AnalyzerRuntimeConfig":
        return cls(
            version=getenv("ANALYZER_VERSION", cls.version),
            batch_concurrency=_int("ANALYZER_BATCH_CONCURRENCY", cls.batch_concurrency),
            run_key=getenv("ANALYZER_RUN_KEY", cls.run_key),
        )
