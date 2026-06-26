"""소스별 base ML 모델 — 런타임 추론 측 (#525 Phase 3).

고빈도 소스(DataLab·Hiring)의 정형 피처를 **공통 타깃 = forward return** 예측으로 바꾸는
base 모델이다(전 종목 패널 풀링으로 학습 — 종목별 모델 금지, D1). 출력은 기존
``ml_inferences`` 계약에 ``model_name=src_datalab/src_hiring`` 으로 적재되어 학습형 메타러너
stacking(Phase 4/WS-C)이 그대로 합류시킨다. OHLCV 는 기존 vol 모델군 유지, Report 는 저빈도라
base 없이 메타러너 피처로 직접 투입(D1).

설계 (기존 vol 모델 레지스트리 관례 준수):
- **가용성 게이트**: ``lightgbm`` 미설치 또는 학습 아티팩트 부재 시 ``predict`` 는 ``None`` 을
  돌려준다 → 해당 소스는 자연스럽게 결측 처리(메타러너 가용성 재정규화가 흡수). 결정론 폴백 없음(D2).
- **별도 run_key**(``SOURCE_RUN_KEY``): src_* 의 타깃은 return 이라 기존 vol 메타결합
  (run_key=ML, 등가평균)에 섞이면 ``combined_vol`` 을 오염시킨다(D4 위반). return 채널은
  WS-C 에서 이 run_key 를 읽어 별도 결합한다. → vol 채널 회귀 0 보장.
- **피처 순서 고정**: 학습·추론이 동일 컬럼 순서를 쓰도록 ``feature_order`` 를 소스 indicator
  스키마에서 파생(필드 추가/삭제에 자동 동기화). 결측은 NaN(LightGBM 네이티브 처리).
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any, Mapping, Sequence

from app.ml.source_features import assemble_features

try:  # 가용성 게이트 — 다른 vol 모델 모듈과 동일 관례.
    import lightgbm as lgb  # noqa: F401

    HAVE_LGBM = True
except ModuleNotFoundError:  # pragma: no cover - 호스트에 백엔드 없을 때
    HAVE_LGBM = False

# src_* base 예측 적재용 run_key. 기존 vol 결합(run_key=ML)과 분리해 vol 채널 불변(D4).
SOURCE_RUN_KEY = "SRC"

# base 모델 대상 소스(고빈도) → assemble_features 키. Report 제외(D1: 메타러너 피처 직접).
SOURCE_MODELS: dict[str, str] = {
    "src_datalab": "datalab",
    "src_hiring": "hiring",
}

# 피처 순서 파생용 기준일(0행 indicator 호출 → 데이터와 무관하게 동일 키셋이 나온다).
_REF_DATE = date(2000, 1, 1)


def feature_order(source: str) -> list[str]:
    """소스의 고정 피처 컬럼 순서. indicator 스키마에서 파생(학습·추론 동일 보장)."""
    empty = assemble_features(_REF_DATE)[source]
    return sorted(empty.keys())


def vectorize(features: Mapping[str, Any], order: Sequence[str]) -> list[float]:
    """피처 dict → 고정 순서 벡터. 결측/None 은 NaN(LightGBM 네이티브 결측)."""
    out: list[float] = []
    for key in order:
        value = features.get(key)
        out.append(float("nan") if value is None else float(value))
    return out


class SourceModel:
    """단일 소스 base 모델 추론기. 아티팩트/백엔드 부재 시 ``predict`` → None(결측)."""

    def __init__(self, model_name: str) -> None:
        if model_name not in SOURCE_MODELS:
            raise ValueError(f"unknown source model: {model_name}")
        self.model_name = model_name
        self.source = SOURCE_MODELS[model_name]
        self.feature_order = feature_order(self.source)
        self._booster: Any | None = None

    @property
    def is_loaded(self) -> bool:
        return self._booster is not None

    def load(self, artifact_path: str) -> "SourceModel":
        """학습된 LightGBM 아티팩트를 적재. 백엔드/파일 부재면 미적재로 남는다(예외 없음)."""
        if not HAVE_LGBM:
            return self
        try:
            self._booster = lgb.Booster(model_file=artifact_path)
        except Exception:  # noqa: BLE001 — 파일 부재/손상 = 미적재(결측), 치명 아님
            self._booster = None
        return self

    def attach(self, booster: Any) -> "SourceModel":
        """이미 메모리에 있는 booster 를 주입(테스트/인-프로세스 학습용)."""
        self._booster = booster
        return self

    def predict(self, features: Mapping[str, Any]) -> float | None:
        """소스 피처 → forward return 예측. 미적재거나 비유한값이면 None(결측)."""
        if self._booster is None:
            return None
        vector = vectorize(features, self.feature_order)
        raw = self._booster.predict([vector])
        value = float(raw[0])
        return value if math.isfinite(value) else None


def predict_sources(
    asof: date,
    *,
    models: Sequence[SourceModel],
    datalab_rows: Sequence[Mapping[str, Any]] = (),
    hiring_rows: Sequence[Mapping[str, Any]] = (),
    lookback_days: int | None = None,
    sector_demand: dict | None = None,
) -> dict[str, float | None]:
    """asof 시점 PIT 피처를 어셈블해 각 base 모델 예측을 ``{model_name: pred|None}`` 로.

    ``run_inference`` 와 같은 역할이되 입력이 OHLCV DataContract 가 아니라 소스 정형 피처다.
    """
    kwargs: dict[str, Any] = {
        "datalab_rows": datalab_rows,
        "hiring_rows": hiring_rows,
        "sector_demand": sector_demand,
    }
    if lookback_days is not None:
        kwargs["lookback_days"] = lookback_days
    features = assemble_features(asof, **kwargs)
    return {m.model_name: m.predict(features[m.source]) for m in models}
