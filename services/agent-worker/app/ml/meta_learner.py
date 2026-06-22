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


@dataclass(frozen=True)
class MetaResult:
    combined_vol: float | None
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
        weights = {
            str(name): float(value)
            for name, value in raw.items()
            if float(value) > 0.0
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
