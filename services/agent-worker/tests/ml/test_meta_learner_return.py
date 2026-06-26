"""메타러너 return 채널 (#525 WS-C) — 균등 폴백·선형 stacker·방향/신뢰도·D4 격리.

vol 채널(combine)과 분리된 신규 채널. base 예측(src_*) + Report 피처(D1)를 결합해
final_score/direction/confidence 를 낸다.
"""

from __future__ import annotations

import json

from app.ml.meta_learner import (
    METHOD_EMPTY,
    METHOD_EQUAL,
    METHOD_LINEAR,
    combine,
    combine_return,
    load_return_model,
)


def test_empty_predictions_is_unknown():
    result = combine_return({})
    assert result.final_score is None
    assert result.direction == "unknown"
    assert result.method == METHOD_EMPTY
    assert result.model_count == 0


def test_equal_fallback_averages_base_predictions():
    result = combine_return({"src_datalab": 0.02, "src_hiring": 0.04})
    assert result.method == METHOD_EQUAL
    assert result.final_score == 0.03  # 평균
    assert result.direction == "positive"
    assert result.confidence == 1.0  # 둘 다 양수 → 방향 합의 100%
    assert result.model_count == 2


def test_mixed_sign_lowers_confidence():
    result = combine_return({"src_datalab": 0.04, "src_hiring": -0.02})
    assert result.final_score == 0.01
    assert result.direction == "positive"
    assert result.confidence == 0.5  # 2개 중 1개만 final 부호와 일치


def test_single_prediction_confidence_is_half():
    result = combine_return({"src_datalab": -0.05})
    assert result.direction == "negative"
    assert result.confidence == 0.5  # 단일 base → 합의 불가, 과신 방지


def test_neutral_band_yields_neutral_direction():
    result = combine_return({"src_datalab": 0.001, "src_hiring": -0.001}, neutral_band=0.01)
    assert result.final_score == 0.0
    assert result.direction == "neutral"


def test_linear_stacker_uses_report_features():
    # final = intercept + 1.0*src_datalab + (-0.5)*report__peer_gap_avg
    model = {"intercept": 0.0, "coef": {"src_datalab": 1.0, "report__peer_gap_avg": -0.5}}
    result = combine_return(
        {"src_datalab": 0.1},
        report_features={"peer_gap_avg": 0.2},
        model=model,
    )
    assert result.method == METHOD_LINEAR
    assert result.final_score == 0.0  # 0.1 - 0.1, Report 피처가 점수를 끌어내림
    assert result.direction == "neutral"
    assert set(result.weight_breakdown) == {"src_datalab", "report__peer_gap_avg"}
    assert result.model_count == 1  # base 예측만 셈(Report 피처는 모델 아님)


def test_linear_stacker_drops_missing_inputs():
    model = {"intercept": 0.01, "coef": {"src_datalab": 1.0, "src_hiring": 2.0}}
    # src_hiring 예측이 없으면 해당 항은 빠진다(자연 강등).
    result = combine_return({"src_datalab": 0.05}, model=model)
    assert result.method == METHOD_LINEAR
    assert result.final_score == round(0.01 + 1.0 * 0.05, 10)
    assert "src_hiring" not in result.weight_breakdown


def test_load_return_model_round_trip_and_filters(tmp_path):
    path = tmp_path / "meta_learner_return.json"
    path.write_text(
        json.dumps(
            {"intercept": 0.001, "coef": {"src_datalab": 0.4, "broken": float("inf")}}
        ),
        encoding="utf-8",
    )
    model = load_return_model(path)
    assert model is not None
    assert model["intercept"] == 0.001
    assert model["coef"] == {"src_datalab": 0.4}  # 비유한 coef 제외


def test_load_return_model_missing_file_is_none(tmp_path):
    assert load_return_model(tmp_path / "absent.json") is None


def test_vol_channel_unchanged_d4():
    # return 채널 추가가 기존 vol combine 을 건드리지 않는다(D4 회귀 0).
    vol = combine({"ewma": 0.2, "har_rv": 0.3})
    assert vol.combined_vol == 0.25
    assert vol.method == METHOD_EQUAL
    assert vol.model_count == 2
