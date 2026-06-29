"""DATALAB source analyzer: collected search-trend rows in, SourceResult out.

Reads only ``RawEvidence.metadata`` (rows + as_of) — no database or external API
access, matching the Analyzer protocol contract.
"""

from __future__ import annotations

from datetime import date

from app.analyzers.config import DataLabRuleConfig
from app.analyzers.datalab.attention import compute_attention_spike
from app.analyzers.datalab.indicators import compute_indicators
from app.schemas.evidence import RawEvidence
from app.schemas.source_result import EvidenceItem, SourceResult


class DataLabAnalyzer:
    source = "DATALAB"

    def __init__(self, config: DataLabRuleConfig | None = None) -> None:
        self._config = config or DataLabRuleConfig.from_env()

    async def analyze(
        self,
        stock_code: str,
        evidence: list[RawEvidence],
    ) -> SourceResult:
        rows = _extract_rows(evidence)
        if not rows:
            return SourceResult(
                source="DATALAB",
                stock_code=stock_code,
                direction="unknown",
                score=0.0,
                summary="분석할 검색 트렌드 데이터가 없습니다 (datalab_raw_details 미적재 또는 카테고리 미매핑).",
                risk_flags=["no_data"],
                data_status="no_signal",
            )

        metadata = evidence[0].metadata
        as_of = _as_of(metadata)
        indicators = compute_indicators(
            rows,
            as_of=as_of,
            lookback_days=self._config.lookback_days,
        )

        # Phase 0 (#525): 결정론 판정/스코어 제거 — 피처 산출만. 방향/점수 verdict 는 내지 않는다
        # (학습형 메타러너의 return 채널이 산출). data_status="no_signal" 로 AGGREGATE 점수 평균에서
        # 제외되고, direction="unknown" 으로 방향 합의에서도 빠진다(소스는 커버리지로만 노출).
        # 피처는 별도 PIT 경로(app/ml/source_features.assemble_features)가 DB 에서 직접 읽는다.
        llm_model, llm_count = _llm_polarity_provenance(rows)

        latest = indicators.latest_observed_date or as_of.isoformat()
        momentum = indicators.momentum_pct
        summary = (
            f"{latest} 기준 최근 {self._config.lookback_days}일 검색 트렌드 {indicators.observations}건 "
            f"피처 산출(모멘텀 {momentum:+.3f}, 스파이크 {indicators.spike_ratio:.2f}). "
            f"판정은 학습형 메타러너가 수행."
            if momentum is not None
            else (
                f"{latest} 기준 최근 {self._config.lookback_days}일 검색 트렌드 "
                f"{indicators.observations}건 피처 산출. 판정은 학습형 메타러너가 수행."
            )
        )
        if llm_count:
            summary += f" (LLM 분류 키워드 {llm_count}건)"

        # Neutral attention-spike layer: only fires when the loader supplied the
        # wider PIT search series. Never touches direction/score (kept unknown/0).
        evidence_items = [
            EvidenceItem(
                title=f"검색 트렌드 피처 {indicators.observations}건",
                summary=f"{latest} 기준 검색 트렌드 지표(피처) 산출 결과",
                published_at=latest,
                source_name="NAVER_DATALAB",
            )
        ]
        risk_flags: list[str] = []
        attention = _attention(metadata, as_of, self._config)
        if attention is not None and attention.risk_flag:
            evidence_items.append(
                EvidenceItem(
                    title=attention.evidence_text,
                    summary="검색 어텐션 급증(중립·주의 근거)",
                    published_at=latest,
                    source_name="NAVER_DATALAB",
                )
            )
            risk_flags.append(attention.risk_flag)

        return SourceResult(
            source="DATALAB",
            stock_code=stock_code,
            direction="unknown",
            score=0.0,
            summary=summary,
            evidence_items=evidence_items,
            risk_flags=risk_flags,
            data_status="no_signal",
            llm_model=llm_model,
            attention_tier=attention.attention_tier if attention else None,
            attention_z=round(attention.attention_z, 3) if attention else None,
            attention_note=attention.evidence_text if (attention and attention.risk_flag) else None,
            expected_fwd_vol_mult=attention.expected_fwd_vol_mult if attention else None,
            expected_fwd_volume_mult=attention.expected_fwd_volume_mult if attention else None,
        )


def _attention(metadata: dict, as_of: date, config: DataLabRuleConfig):
    """Compute the neutral attention spike from the loader's wider PIT series.

    Returns None when no ``attention_series`` was supplied (e.g. legacy callers /
    unit tests that only pass ``rows``) or history is too short — so the feature
    is strictly additive and never changes the directional/feature output.
    """
    series = metadata.get("attention_series")
    if not series:
        return None
    return compute_attention_spike(series, as_of=as_of, config=config)


def _extract_rows(evidence: list[RawEvidence]) -> list[dict]:
    for item in evidence:
        rows = item.metadata.get("rows")
        if rows:
            return rows
    return []


def _llm_polarity_provenance(rows: list[dict]) -> tuple[str | None, int]:
    """Return (llm_model, count) for rows whose polarity was LLM-classified.

    ``llm_model`` is the first non-empty ``polarity_model`` among LLM-sourced rows,
    or None when no keyword's polarity came from the LLM lane. Pure read — it does
    not affect indicators or the score (Scope A).
    """
    llm_rows = [row for row in rows if row.get("polarity_source") == "llm"]
    model = next((row.get("polarity_model") for row in llm_rows if row.get("polarity_model")), None)
    return model, len(llm_rows)


def _as_of(metadata: dict) -> date:
    raw = metadata.get("as_of")
    if isinstance(raw, date):
        return raw
    if raw:
        try:
            return date.fromisoformat(str(raw)[:10])
        except ValueError:
            pass
    return date.today()
