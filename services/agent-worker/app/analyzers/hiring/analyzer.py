"""HIRING source analyzer: collected job-postings rows in, SourceResult out.

Reads only ``RawEvidence.metadata`` (rows + as_of) — no database or external API
access, matching the Analyzer protocol contract.
"""

from __future__ import annotations

from collections import Counter
from datetime import date

from app.analyzers.config import HiringRuleConfig
from app.analyzers.hiring.indicators import compute_decayed_activity, compute_indicators
from app.analyzers.hiring.rules import evaluate_decayed, evaluate_indicators
from app.schemas.evidence import RawEvidence
from app.schemas.source_result import EvidenceItem, SourceResult


class HiringAnalyzer:
    source = "HIRING"

    def __init__(self, config: HiringRuleConfig | None = None) -> None:
        self._config = config or HiringRuleConfig.from_env()

    async def analyze(
        self,
        stock_code: str,
        evidence: list[RawEvidence],
    ) -> SourceResult:
        rows = _extract_rows(evidence)
        metadata = evidence[0].metadata if evidence else {}
        as_of = _as_of(metadata)
        # opt-in: 공고별 시간감쇠 경로(로더가 metadata["postings"] 제공 시). no-data 가드보다
        # 먼저 본다 — 감쇠 유효창(≤180d)은 momentum rows(90d) 보다 넓어, 90일보다 오래된 공고만
        # 있는 종목은 momentum rows 가 비어도 감쇠 경로로 가야 한다. 게이트 off 이거나 목록이
        # 없으면 아래 현행 momentum 경로 그대로(회귀 보장).
        postings = metadata.get("postings")
        if self._config.posting_decay_enabled and postings:
            return self._analyze_decayed(stock_code, postings, as_of)
        if not rows:
            return SourceResult(
                source="HIRING",
                stock_code=stock_code,
                direction="unknown",
                score=0.0,
                summary="분석할 채용 데이터가 없습니다 (hiring_raw_details 미적재).",
                risk_flags=["no_data"],
                data_status="no_signal",
            )
        indicators = compute_indicators(
            rows,
            as_of=as_of,
            lookback_days=self._config.lookback_days,
            sector_demand=metadata.get("sector_demand"),
        )
        # 메타러너 폐기(2026-07) 후 결정론 verdict 복원 — #525 "피처 전용"은 학습형 메타러너가
        # 판정을 대신한다는 전제였으나 그 채널이 폐기됐다. 이제 채용도 특허·DART·리포트와 동일하게
        # rules.evaluate_indicators 로 방향/점수를 내고 AGGREGATE 등가중 통합 점수에 산입된다.
        assessment = evaluate_indicators(indicators, self._config)

        risk_flags = list(assessment.risk_flags)
        data_status = "ok"
        if assessment.direction == "unknown":
            data_status = "no_signal"
        elif {"insufficient_history", "stale_data", "low_base"} & set(risk_flags):
            data_status = "partial"

        latest = indicators.latest_observed_date or as_of.isoformat()
        momentum = indicators.momentum_pct
        count = metadata.get("posting_count") or indicators.observations or 0
        head = (
            f"최근 {self._config.lookback_days}일 채용공고 {count}건"
            + (f"(직전 대비 {momentum * 100:+.0f}%)" if momentum is not None else "")
        )

        # Descriptive surfacing — 기술스택만. job_title 은 노이즈가 많고, keyword 선두 [..]도
        # 소스마다 사업부/회사명/고용형태(계약직·경력)/스케줄이 섞여(실측) 신뢰할 수 있는
        # '사업부'가 아니라서 서술에서 제외한다.
        top_techs = _hiring_focus(rows)
        focus_bits: list[str] = []
        if top_techs:
            focus_bits.append(f"주요 기술: {'·'.join(top_techs)}")

        summary = (
            head
            + (", " + " · ".join(focus_bits) if focus_bits else "")
            + f" → 방향 {assessment.direction}, 점수 {assessment.score:+.3f}."
        )

        evidence_items: list[EvidenceItem] = [
            EvidenceItem(
                title=highlight,
                summary=f"{latest} 기준 채용 지표 산출 결과",
                published_at=latest,
                source_name="HIRING",
            )
            for highlight in assessment.highlights
        ]
        if focus_bits:
            evidence_items.append(
                EvidenceItem(
                    title="채용 기술 포커스",
                    summary=" · ".join(focus_bits),
                    published_at=latest,
                    source_name="HIRING",
                )
            )

        return SourceResult(
            source="HIRING",
            stock_code=stock_code,
            direction=assessment.direction,
            score=assessment.score,
            summary=summary,
            evidence_items=evidence_items,
            risk_flags=risk_flags,
            data_status=data_status,
        )

    def _analyze_decayed(
        self, stock_code: str, postings: list[dict], as_of: date
    ) -> SourceResult:
        """공고별 지수 시간감쇠 경로: 게시일 나이에 반감기 가중한 활동강도 → verdict.
        유효창은 대기업/일반 차등. 창 밖·마감 공고는 산입에서 빠진다(DB raw 는 보존)."""
        is_large = stock_code in self._config.large_cap_tickers
        window = self._config.cycle_days_large if is_large else self._config.cycle_days_default
        half_life = max(1.0, window * self._config.decay_half_life_ratio)
        scale = self._config.activity_scale_large if is_large else self._config.activity_scale
        # 옵션3(long_term_avg)일 때만 장기창을 넘겨 회사 장기 채용률 baseline 을 산출한다.
        # 기본(prior_window)이면 None → 기존 반분할 급감 감지 그대로(회귀 보장).
        long_term = (
            self._config.long_term_window_days
            if self._config.baseline_mode == "long_term_avg"
            else None
        )
        indicators = compute_decayed_activity(
            postings,
            as_of=as_of,
            window_days=window,
            half_life_days=half_life,
            long_term_window_days=long_term,
        )
        assessment = evaluate_decayed(indicators, self._config, activity_scale=scale)

        data_status = "ok"
        if assessment.direction == "unknown":
            data_status = "no_signal"  # 유효창 내 활성 공고 없음 → 집계 제외·FE 만료
        elif "insufficient_history" in assessment.risk_flags:
            data_status = "partial"

        tier = "대기업" if is_large else "일반"
        latest = indicators.latest_posting_date or as_of.isoformat()
        summary = (
            f"활성 채용공고 {indicators.active_count}건"
            f"(유효창 {window}일·{tier}, 감쇠활동 {indicators.decayed_activity:.2f}) → "
            f"방향 {assessment.direction}, 점수 {assessment.score:+.3f}."
        )
        # 집계 근거(감쇠 하이라이트) + 공고별 근거(게시일=published_at, 마감일). FE 는 published_at
        # 으로 "N일 전"을 렌더 시 계산한다(절대날짜만 노출, age 숫자 박제 금지).
        evidence_items = [
            EvidenceItem(
                title=highlight,
                summary=f"{latest} 기준 채용 활동(시간감쇠) 산출 결과",
                published_at=latest,
                source_name="HIRING",
            )
            for highlight in assessment.highlights
        ]
        evidence_items.extend(
            EvidenceItem(
                title="채용공고",
                summary=(f"마감 {closing}" if closing else "상시/마감일 미상"),
                published_at=posted,  # 게시일(절대날짜) — FE 가 오늘−게시일로 age 계산
                source_name="HIRING",
            )
            for posted, closing in indicators.active_postings
        )
        return SourceResult(
            source="HIRING",
            stock_code=stock_code,
            direction=assessment.direction,
            score=assessment.score,
            summary=summary,
            evidence_items=evidence_items,
            risk_flags=list(assessment.risk_flags),
            data_status=data_status,
        )


def _extract_rows(evidence: list[RawEvidence]) -> list[dict]:
    for item in evidence:
        rows = item.metadata.get("rows")
        if rows:
            return rows
    return []


def _hiring_focus(rows: list[dict]) -> list[str]:
    """Top tech stacks across the postings (descriptive only — never affects the
    score). Returns [] when rows carry no ``tech_stack``.

    직무명(job_title)은 스케줄 표기·'모집' 같은 노이즈가 많아 서술에서 제외한다 — 사업부(keyword
    선두 [..], 로더가 metadata.business_units 로 제공)와 기술스택만 노출한다.
    """
    tech_counter: Counter[str] = Counter()
    for row in rows:
        techs = row.get("tech_stack") or []
        if isinstance(techs, str):
            techs = [t.strip() for t in techs.split(",")]
        tech_counter.update(t for t in techs if t)
    return [t for t, _ in tech_counter.most_common(3)]


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
