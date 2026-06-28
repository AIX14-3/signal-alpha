"""메타러너 결합 (stacking) — architecture.mermaid의 "메타러너 결합 stacking (학습된 가중)".

ML/DL 추론 단계(``inference.py``)가 만든 게이트 통과 모델별 변동성 예측(pred_vol)을 하나의
결합 변동성 추정치 + 신뢰도로 합친다. 결합 가중은 오프라인 학습 산출물(stacking weights)을
``artifacts/meta_learner.json`` 에서 로드한다(런타임은 추론만). 산출물이 없거나 비면 **균등
가중 폴백** 으로 동작해 기존 거동(가중치 없는 평균)을 보존한다.

이 단계는 **결정론적 결합** 이다(LLM 아님). 방향성 신호(positive/negative)는 소스 분석기와
기존 AGGREGATE_SIGNAL이 그대로 책임지고, 여기서는 변동성/리스크 크기와 모델 합의 신뢰도만
산출한다 — 끝단 LLM(PR5)이 이 수치를 바꾸지 않고 설명만 한다.
"""

from __future__ import annotations

import json
import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

# 학습된 stacking 가중 기본 경로. 없으면 균등 가중 폴백(기존 거동 보존).
DEFAULT_ARTIFACT_PATH = Path(__file__).resolve().parent / "artifacts" / "meta_learner.json"

METHOD_STACKING = "stacking"
METHOD_EQUAL = "equal_fallback"
METHOD_EMPTY = "empty"
# return 채널 학습형 결합(부호 있는 선형 stacker). vol 의 비음수 가중 평균과 구분.
METHOD_LINEAR = "linear_stacking"

# 예측 수익률(부호값) → 0-100 'AI 예측 점수' 변환 기울기(tanh). 50=중립, 상승↑/하락↓.
# +3% → ~64, 0% → 50, -2% → ~41 (사용자 승인 매핑). 헤드라인(통합)·소스별 예측률에 공통 적용.
RETURN_SCORE_STEEPNESS = 9.6


def return_to_score_100(return_value: float, *, steepness: float = RETURN_SCORE_STEEPNESS) -> float:
    """예측 수익률 → 0-100 점수. tanh 로 50 중심에 매핑(상승↑/하락↓), 극단값은 포화."""
    return round(50.0 + 50.0 * math.tanh(steepness * return_value), 2)

# return 채널 학습 산출물 기본 경로. 없으면 base 예측 균등 평균 폴백.
DEFAULT_RETURN_ARTIFACT_PATH = (
    Path(__file__).resolve().parent / "artifacts" / "meta_learner_return.json"
)


@dataclass(frozen=True)
class MetaResult:
    combined_vol: float | None
    confidence: float
    method: str
    model_count: int
    weight_breakdown: dict[str, float]


@dataclass(frozen=True)
class ReturnMetaResult:
    """return 채널 결합 결과 (#525 D4 신규 채널). vol 채널(MetaResult)과 병행."""

    final_score: float | None
    direction: str  # positive | negative | neutral | unknown
    confidence: float
    method: str
    model_count: int
    weight_breakdown: dict[str, float]


def load_weights(path: str | Path | None = None) -> dict[str, float] | None:
    """학습된 stacking 가중을 로드. 파일/포맷이 없거나 깨지면 None(폴백 신호).

    포맷: ``{"weights": {"ewma": 0.2, "har_rv": 0.5, ...}}`` (음수/0 제외).
    환경변수 ``ML_META_LEARNER_ARTIFACT`` 로 경로 오버라이드 가능.
    """
    resolved = Path(path or os.getenv("ML_META_LEARNER_ARTIFACT") or DEFAULT_ARTIFACT_PATH)
    if not resolved.is_file():
        return None
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        raw = payload.get("weights", payload)
        # 비유한값(NaN/inf)은 제외 — 과적합/깨진 학습 산출물이 추론을 지배하지 못하게
        # 한다(>0 필터는 NaN은 막지만 inf는 통과시키므로 isfinite 가드를 더한다).
        # 학습측 과적합 규율(walk-forward OOF·정규화·피처 집계)은
        # docs/archive/design/meta-learner-training.md 참고.
        weights = {
            str(name): float(value)
            for name, value in raw.items()
            if math.isfinite(float(value)) and float(value) > 0.0
        }
    except (ValueError, AttributeError, TypeError):
        return None
    return weights or None


def combine(
    predictions: Mapping[str, float],
    *,
    weights: Mapping[str, float] | None = None,
) -> MetaResult:
    """모델별 pred_vol을 stacking(학습 가중) 또는 균등 폴백으로 결합.

    ``predictions`` 는 추론에 성공한 모델만(실패/비유한값은 호출 전에 제외). 학습 가중은 현재
    존재하는 모델 부분집합으로 재정규화한다 — 일부 모델이 빠져도(가용성 게이트) 안전.
    """
    preds = {name: float(v) for name, v in predictions.items() if math.isfinite(float(v))}
    if not preds:
        return MetaResult(None, 0.0, METHOD_EMPTY, 0, {})

    applicable = (
        {name: weights[name] for name in preds if name in weights and weights[name] > 0.0}
        if weights
        else {}
    )
    if applicable:
        total = sum(applicable.values())
        normalized = {name: w / total for name, w in applicable.items()}
        method = METHOD_STACKING
        # 가중 적용 대상만 결합(학습 가중이 다루는 모델 부분집합).
        contributing = {name: preds[name] for name in normalized}
    else:
        normalized = {name: 1.0 / len(preds) for name in preds}
        method = METHOD_EQUAL
        contributing = preds

    # combined_vol·confidence·model_count·weight_breakdown 모두 *결합에 실제 기여한* 동일
    # 집합(contributing) 기준 — stacking에서 가중 밖 모델을 셈에서만 포함시키던 불일치 제거.
    combined = sum(normalized[name] * contributing[name] for name in normalized)

    return MetaResult(
        combined_vol=round(combined, 10),
        confidence=_confidence(contributing),
        method=method,
        model_count=len(contributing),
        weight_breakdown={name: round(w, 6) for name, w in normalized.items()},
    )


# ── return 채널 (#525 WS-C) ────────────────────────────────────────────────
# vol 채널과 분리: 입력 = 소스 base 예측(src_*, forward return) + 저빈도 Report 피처(D1).
# 학습형 결합은 **부호 있는 선형 stacker**(coef 음수 가능 — 리스크 피처는 수익률에 음의 기여).
# 따라서 vol 의 ``load_weights``(비음수 share, >0 필터)를 재사용하지 않고 별도 로더를 둔다.

REPORT_FEATURE_PREFIX = "report__"


def load_return_model(path: str | Path | None = None) -> dict | None:
    """return 채널 선형 stacker 산출물 로드. 없으면 None(base 예측 균등 폴백 신호).

    포맷: ``{"intercept": 0.001, "coef": {"src_datalab": 0.4, "report__peer_gap_avg": -0.2, ...}}``.
    비유한 coef 는 제외. 환경변수 ``ML_META_LEARNER_RETURN_ARTIFACT`` 로 경로 오버라이드.
    """
    resolved = Path(
        path or os.getenv("ML_META_LEARNER_RETURN_ARTIFACT") or DEFAULT_RETURN_ARTIFACT_PATH
    )
    if not resolved.is_file():
        return None
    try:
        payload = json.loads(resolved.read_text(encoding="utf-8"))
        raw_coef = payload.get("coef", {})
        coef = {
            str(name): float(value)
            for name, value in raw_coef.items()
            if math.isfinite(float(value))
        }
        intercept = float(payload.get("intercept", 0.0))
        if not math.isfinite(intercept):
            intercept = 0.0
    except (ValueError, AttributeError, TypeError):
        return None
    if not coef:
        return None
    return {"intercept": intercept, "coef": coef}


def combine_return(
    base_predictions: Mapping[str, float],
    *,
    report_features: Mapping[str, float | None] | None = None,
    model: Mapping | None = None,
    neutral_band: float = 0.0,
) -> ReturnMetaResult:
    """소스 base forward-return 예측(+Report 피처)을 return 채널로 결합.

    - 학습 모델 있음 → 부호 있는 **선형 stacker**: ``final = intercept + Σ coef[k]·input[k]``
      (가용 입력만; src_* base 예측 ∪ ``report__*`` 피처). 결측 입력은 항을 빼서 자연 강등.
    - 학습 모델 없음 → **base 예측 균등 평균** 폴백(Report 피처는 수익률 스케일이 아니라 제외).
    direction 은 ``final`` 부호(±neutral_band 중립대), confidence 는 base 예측의 방향 합의.
    """
    preds = {
        name: float(v) for name, v in base_predictions.items() if v is not None and math.isfinite(float(v))
    }
    if not preds:
        return ReturnMetaResult(None, "unknown", 0.0, METHOD_EMPTY, 0, {})

    inputs: dict[str, float] = dict(preds)
    if report_features:
        for name, value in report_features.items():
            if value is not None and math.isfinite(float(value)):
                key = name if str(name).startswith(REPORT_FEATURE_PREFIX) else f"{REPORT_FEATURE_PREFIX}{name}"
                inputs[key] = float(value)

    coef = dict(model.get("coef", {})) if model else {}
    applicable = {k: coef[k] for k in inputs if k in coef}
    if applicable:
        final = float(model.get("intercept", 0.0)) + sum(
            applicable[k] * inputs[k] for k in applicable
        )
        method = METHOD_LINEAR
        breakdown = {k: round(applicable[k], 6) for k in applicable}
        # 기여 base 예측(부호 합의용)·모델 수: 선형식에 들어간 src_* 만(Report 피처는 모델 아님).
        contributing = {k: preds[k] for k in preds if k in applicable}
    else:
        final = sum(preds.values()) / len(preds)
        method = METHOD_EQUAL
        breakdown = {name: round(1.0 / len(preds), 6) for name in preds}
        contributing = preds

    return ReturnMetaResult(
        final_score=round(final, 10),
        direction=_direction(final, neutral_band),
        confidence=_return_confidence(contributing, final),
        method=method,
        model_count=len(contributing),
        weight_breakdown=breakdown,
    )


def _direction(final_score: float, neutral_band: float) -> str:
    if final_score > neutral_band:
        return "positive"
    if final_score < -neutral_band:
        return "negative"
    return "neutral"


def _return_confidence(base_preds: Mapping[str, float], final_score: float) -> float:
    """base 예측의 방향 합의도 [0,1]. 부호가 final 과 일치하는 비율.

    단일 base 예측은 합의를 말할 수 없어 0.5(과신 방지, vol _confidence 관례와 동일).
    final 이 중립(0)이면 방향 합의가 없으므로 0.
    """
    values = list(base_preds.values())
    if len(values) <= 1:
        return 0.5
    target = _sign(final_score)
    if target == 0:
        return 0.0
    agree = sum(1 for v in values if _sign(v) == target)
    return round(agree / len(values), 4)


def _sign(value: float) -> int:
    if value > 0:
        return 1
    if value < 0:
        return -1
    return 0


def _confidence(preds: Mapping[str, float]) -> float:
    """모델 합의도 기반 신뢰도 [0,1].

    분산이 작을수록(모델들이 비슷한 변동성을 예측) 신뢰↑. 변동계수(cv=std/mean)를 써서
    ``1 - cv`` 를 [0,1]로 클립. 단일 모델은 합의를 말할 수 없으므로 0.5로 둔다(과신 방지 —
    AGGREGATE_SIGNAL의 단일소스 consensus=50과 같은 취지).
    """
    values = list(preds.values())
    if len(values) <= 1:
        return 0.5
    mean = sum(values) / len(values)
    if mean <= 0:
        return 0.0
    variance = sum((v - mean) ** 2 for v in values) / len(values)
    cv = math.sqrt(variance) / mean
    return round(max(0.0, min(1.0, 1.0 - cv)), 4)
