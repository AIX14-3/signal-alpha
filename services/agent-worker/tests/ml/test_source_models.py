"""소스별 base 모델 (#525 Phase 3) — 피처 벡터화·가용성 폴백·walk-forward OOF 검증.

순수 부분은 백엔드 없이 검증한다. 학습 end-to-end 는 lightgbm 가용 시에만(importorskip).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from app.ml.source_features import assemble_features
from app.ml.source_models import (
    SOURCE_MODELS,
    SourceModel,
    feature_order,
    predict_sources,
    vectorize,
)
from app.ml.source_training import build_panel, walk_forward_splits


def test_feature_order_matches_assemble_keys():
    for model_name, source in SOURCE_MODELS.items():
        order = feature_order(source)
        keys = set(assemble_features(date(2026, 1, 1))[source].keys())
        assert set(order) == keys
        assert order == sorted(order)  # 고정·결정적 순서


def test_vectorize_preserves_order_and_maps_missing_to_nan():
    order = ["a", "b", "c"]
    vec = vectorize({"a": 1.0, "c": 3.0, "b": None}, order)
    assert vec[0] == 1.0 and vec[2] == 3.0
    assert vec[1] != vec[1]  # NaN (자기 자신과 != )


def test_unloaded_model_predicts_none():
    # 아티팩트/백엔드 부재 → None(결측). 결정론 폴백 없음(D2).
    model = SourceModel("src_datalab")
    assert model.is_loaded is False
    assert model.predict({"momentum_pct": 0.2}) is None


def test_predict_sources_returns_none_when_unloaded():
    models = [SourceModel("src_datalab"), SourceModel("src_hiring")]
    preds = predict_sources(date(2026, 6, 1), models=models)
    assert preds == {"src_datalab": None, "src_hiring": None}


def test_build_panel_drops_unlabeled_and_vectorizes():
    samples = [
        {"asof": date(2026, 1, 1), "features": {"momentum_pct": 0.1}, "label": 0.02},
        {"asof": date(2026, 1, 2), "features": {"momentum_pct": 0.3}, "label": None},  # 라벨 결측 제외
        {"asof": date(2026, 1, 3), "features": {"momentum_pct": 0.2}, "label": 0.01},
    ]
    panel = build_panel(samples, source="datalab")
    assert panel.y == [0.02, 0.01]
    assert len(panel.X) == 2
    assert panel.feature_names == feature_order("datalab")


def test_walk_forward_splits_are_leak_free():
    # 10일 × 2종목 패널 → 행별 asof. 각 폴드 train 의 모든 asof < valid 의 모든 asof.
    days = [date(2026, 1, 1) + timedelta(days=d) for d in range(10)]
    asofs = [d for d in days for _ in range(2)]  # 종목 2개 패널 풀링

    folds = walk_forward_splits(asofs, n_splits=4)

    assert len(folds) == 4
    for train_idx, valid_idx in folds:
        assert train_idx and valid_idx
        max_train = max(asofs[i] for i in train_idx)
        min_valid = min(asofs[i] for i in valid_idx)
        assert max_train < min_valid  # train 은 valid 이전 시점만(D3)


def test_walk_forward_requires_enough_distinct_dates():
    with pytest.raises(ValueError):
        walk_forward_splits([date(2026, 1, 1), date(2026, 1, 2)], n_splits=4)


def test_train_and_predict_end_to_end():
    pytest.importorskip("lightgbm")
    from app.ml.source_training import train_booster

    # 합성 패널: 라벨 = momentum 선형 + 잡음. base 모델이 부호를 학습하는지 확인.
    base = date(2026, 1, 1)
    samples = []
    for d in range(60):
        asof = base + timedelta(days=d)
        for k in range(4):  # 패널 풀링(전 종목)
            momentum = (d % 7 - 3) / 10.0 + k * 0.01
            samples.append(
                {
                    "asof": asof,
                    "features": {"momentum_pct": momentum, "spike_ratio": 0.0},
                    "label": 0.5 * momentum,
                }
            )
    panel = build_panel(samples, source="datalab")
    booster = train_booster(panel, n_splits=4, num_boost_round=50)

    model = SourceModel("src_datalab").attach(booster)
    high = model.predict({"momentum_pct": 0.25, "spike_ratio": 0.0})
    low = model.predict({"momentum_pct": -0.25, "spike_ratio": 0.0})
    assert high is not None and low is not None
    assert high > low  # 양의 momentum → 더 큰 forward return 예측
