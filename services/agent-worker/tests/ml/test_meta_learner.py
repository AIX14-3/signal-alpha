"""메타러너 stacking 결합: 학습 가중 / 균등 폴백 / 신뢰도 / 가중 로딩."""

from __future__ import annotations

import json

from app.ml.meta_learner import (
    METHOD_EMPTY,
    METHOD_EQUAL,
    METHOD_STACKING,
    combine,
    load_weights,
)


def test_combine_empty_predictions() -> None:
    result = combine({})
    assert result.combined_vol is None
    assert result.method == METHOD_EMPTY
    assert result.model_count == 0
    assert result.confidence == 0.0


def test_combine_equal_fallback_is_plain_mean() -> None:
    result = combine({"ewma": 0.2, "har_rv": 0.4})  # no weights → equal
    assert result.method == METHOD_EQUAL
    assert result.combined_vol == 0.3  # (0.2 + 0.4) / 2
    assert result.weight_breakdown == {"ewma": 0.5, "har_rv": 0.5}
    assert result.model_count == 2


def test_combine_stacking_uses_normalized_learned_weights() -> None:
    preds = {"ewma": 0.2, "har_rv": 0.4}
    result = combine(preds, weights={"ewma": 1.0, "har_rv": 3.0})
    assert result.method == METHOD_STACKING
    # weights renormalize to 0.25 / 0.75 → 0.25*0.2 + 0.75*0.4 = 0.35
    assert result.combined_vol == 0.35
    assert result.weight_breakdown == {"ewma": 0.25, "har_rv": 0.75}


def test_combine_stacking_restricts_to_present_models() -> None:
    # weights mention a model that isn't present; present-only subset is renormalized
    preds = {"ewma": 0.2, "har_rv": 0.4}
    result = combine(preds, weights={"ewma": 1.0, "har_rv": 1.0, "garch": 5.0})
    assert result.method == METHOD_STACKING
    assert result.combined_vol == 0.3  # equal over the two present models
    assert set(result.weight_breakdown) == {"ewma", "har_rv"}


def test_combine_stacking_counts_only_contributing_models() -> None:
    # 가중이 일부 모델만 다루면 model_count·confidence·weight_breakdown 모두 그 부분집합 기준
    # (combined_vol과 동일 집합) — 가중 밖 모델(garch)을 셈에만 포함하던 불일치 제거.
    preds = {"ewma": 0.2, "har_rv": 0.4, "garch": 0.9}
    result = combine(preds, weights={"ewma": 1.0, "har_rv": 1.0})
    assert result.method == METHOD_STACKING
    assert result.combined_vol == 0.3  # 0.5*0.2 + 0.5*0.4 (garch 제외)
    assert result.model_count == 2
    assert len(result.weight_breakdown) == result.model_count
    # confidence는 결합에 쓰인 {ewma, har_rv}만 기준 — 전체(garch 포함)와 다른 값
    assert result.confidence == combine({"ewma": 0.2, "har_rv": 0.4}).confidence


def test_combine_falls_back_when_no_weight_applies() -> None:
    # learned weights exist but cover none of the present models → equal fallback
    result = combine({"ewma": 0.2}, weights={"garch": 1.0})
    assert result.method == METHOD_EQUAL
    assert result.combined_vol == 0.2


def test_confidence_single_model_is_half_and_agreement_is_high() -> None:
    assert combine({"ewma": 0.3}).confidence == 0.5
    # near-identical predictions ⇒ low dispersion ⇒ high confidence
    agree = combine({"a": 0.30, "b": 0.31, "c": 0.30}).confidence
    disagree = combine({"a": 0.1, "b": 0.9}).confidence
    assert agree > 0.9
    assert disagree < agree


def test_load_weights_missing_returns_none(tmp_path) -> None:
    assert load_weights(tmp_path / "nope.json") is None


def test_load_weights_reads_json_and_drops_nonpositive(tmp_path) -> None:
    path = tmp_path / "meta_learner.json"
    path.write_text(
        json.dumps({"weights": {"ewma": 0.5, "har_rv": 0.0, "garch": -1.0}}),
        encoding="utf-8",
    )
    assert load_weights(path) == {"ewma": 0.5}


def test_load_weights_env_override(monkeypatch, tmp_path) -> None:
    path = tmp_path / "w.json"
    path.write_text(json.dumps({"weights": {"har_rv": 2.0}}), encoding="utf-8")
    monkeypatch.setenv("ML_META_LEARNER_ARTIFACT", str(path))
    assert load_weights() == {"har_rv": 2.0}
