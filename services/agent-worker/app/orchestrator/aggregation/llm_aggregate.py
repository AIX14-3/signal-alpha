"""LLM 통합 판정(aggregator) 프로덕션 배선 — ``LLM_AGGREGATE_ENABLED`` 게이트.

⚠️ "숫자는 결정론이 소유" 불변식의 의도적 폐기(2026-07-13 승인) 2단계: 소스 점수에
이어 **통합 점수·헤드라인도 LLM 이 판단**한다(등가중 평균이 아니라 LLM 이 소스별
가중을 스스로 정해 ``blend_basis`` 로 남긴다).

## 결정론이 유지하는 것 (오버라이드하지 않는 필드)
consensus_score / source_agreement / warning_level / score_breakdown / risk_flags /
bull·bear_point / is_published — FE 계약과 데이터 품질 사실은 LLM 이 알 수 없거나
정직성 장치라 기존 결정론 헬퍼 값을 그대로 둔다. ``confidence`` 컬럼도 기존처럼
consensus(0-100)가 가고, LLM confidence[0, 0.85]는 ``score_breakdown._meta`` 로만
흐른다 — **두 축을 절대 섞지 않는다.**

## 실패 시
LLM 호출/계약 실패면 결정론 blend 를 그대로 발행하고 ``llm_aggregate_error`` 만
남긴다(발행 공백 없음·관측 가능).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.analyzers.llm_aggregator import aggregate_cohort, blend_basis
from app.analyzers.llm_scorer import StockScore

if TYPE_CHECKING:  # pragma: no cover
    from app.core.config import Settings

logger = logging.getLogger(__name__)


def build_per_source_scores(coarse: list[Any], ticker: str) -> dict[str, StockScore]:
    """fan-in 이 모은 소스별 결과 → LLM 통합 판정 입력(``StockScore``) 복원.

    failed 소스는 애초에 제외(집계도 제외한다). no_signal 은 포함하되 —
    ``llm_aggregator.build_prompt`` 가 no_signal 소스를 프롬프트의 판단 재료에서
    빼고 ``sources_with_no_signal`` 로만 노출한다(기존 계약)."""
    out: dict[str, StockScore] = {}
    for r in coarse:
        if r.data_status == "failed":
            continue
        evidence = list(r.highlights) or ([r.summary] if r.summary else [])
        out[r.source] = StockScore(
            ticker=ticker,
            score=float(r.score),
            # 결정론 경로 결과(llm_confidence=None)는 중간값으로 — 프롬프트 참고치일 뿐
            # 판정 숫자에 산술로 들어가지 않는다.
            confidence=float(r.llm_confidence) if r.llm_confidence is not None else 0.5,
            no_signal=(r.data_status == "no_signal"),
            evidence=evidence,
        )
    return out


async def maybe_llm_aggregate(
    settings: "Settings",
    *,
    ticker: str,
    name: str,
    signal_date: Any,
    coarse: list[Any],
    aggregate: dict[str, Any],
    client_factory: Any | None = None,
) -> dict[str, Any]:
    """플래그 on 이고 판단 재료가 있으면 aggregate 의 LLM 소유 필드를 오버라이드한다.

    반환은 새 dict — 실패하면 원본 + ``llm_aggregate_error`` 만 추가."""
    if not settings.llm_aggregate_enabled:
        return aggregate
    if aggregate.get("scoring_count", 0) <= 0:
        return aggregate  # 판단 재료가 없다 — 중립 발행 경로 유지
    per_source = build_per_source_scores(coarse, ticker)
    if not per_source:
        return aggregate

    try:
        if client_factory is not None:
            client = client_factory()
        else:
            from app.clients.json_llm import build_json_client

            client = build_json_client(
                settings.llm_scoring_provider, settings.llm_scoring_model
            )
        verdicts = await aggregate_cohort(
            client,
            asof=str(signal_date),
            per_stock={ticker: per_source},
            names={ticker: name or ticker},
        )
        verdict = next(v for v in verdicts if v.ticker == ticker)
    except Exception as exc:  # noqa: BLE001 — 실패 시 결정론 blend 그대로 발행
        logger.warning("LLM aggregate 실패 (%s) — 결정론 blend 유지: %s", ticker, exc)
        out = dict(aggregate)
        out["llm_aggregate_error"] = str(exc)[:300]
        return out

    out = dict(aggregate)
    # LLM 소유 필드 — 스케일 규약: final_score 컬럼은 0-100 이므로 반드시 score_100.
    out["signal"] = verdict.signal
    out["final_score"] = verdict.score_100
    out["blend_basis"] = blend_basis(verdict)
    out["summary"] = verdict.headline
    out["positive_evidence"] = list(verdict.positive_evidence)
    # 데이터 품질 사실(stale/missing 등 결정론 주의 근거)은 LLM 이 모른다 — 뒤에 보존.
    out["caution_evidence"] = _dedupe(
        list(verdict.caution_evidence) + list(aggregate.get("caution_evidence") or [])
    )
    out["needs_review"] = bool(aggregate.get("needs_review")) or verdict.conflict
    out["llm_aggregate"] = {
        "model": getattr(client, "model", None),
        "confidence": verdict.confidence,  # [0, 0.85] — consensus(0-100)와 다른 축
        "conflict": verdict.conflict,
    }
    return out


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out
