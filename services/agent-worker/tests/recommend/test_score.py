"""run_recommend.score_recommendation 단위 테스트 — 결정론 추천 점수 공식 고정."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # services/agent-worker

from run_recommend import score_recommendation


def test_final_basis_uses_signal_score_confidence_and_vol_inverse():
    # final_score=72, confidence=80, combined_vol=0.0744, penalty=5
    # dir_w=0.72, conf_w=0.80, vol_w=1/(1+5*0.0744)=0.72886 → 100*0.72*0.8*0.72886≈41.98
    out = score_recommendation(
        final_row={"signal": "positive", "final_score": 72.0, "confidence": 80.0},
        meta_row={"combined_vol": 0.0744, "confidence": 0.65},
        vol_penalty=5.0,
    )
    assert out["basis"] == "final"
    assert out["signal"] == "positive"
    assert out["recommendation_score"] == 41.98
    assert out["components"]["dir_w"] == 0.72


def test_meta_basis_is_neutral_direction():
    # 발행 신호 없음 → dir_w=0.5, conf_w=meta.confidence(0..1)
    out = score_recommendation(
        final_row=None,
        meta_row={"combined_vol": 0.0730, "confidence": 0.7485},
        vol_penalty=5.0,
    )
    assert out["basis"] == "meta"
    assert out["signal"] is None
    assert out["final_score"] is None
    assert out["components"]["dir_w"] == 0.5
    # 100 * 0.5 * 0.7485 * (1/(1+5*0.073)) ≈ 27.42
    assert out["recommendation_score"] == 27.42


def test_lower_volatility_ranks_higher():
    """변동성 역가중: 같은 신호/신뢰도면 변동성 낮은 쪽 점수가 높다."""
    low = score_recommendation(
        final_row=None, meta_row={"combined_vol": 0.05, "confidence": 0.7}, vol_penalty=5.0
    )
    high = score_recommendation(
        final_row=None, meta_row={"combined_vol": 0.15, "confidence": 0.7}, vol_penalty=5.0
    )
    assert low["recommendation_score"] > high["recommendation_score"]


def test_missing_meta_vol_defaults_to_no_penalty():
    out = score_recommendation(
        final_row={"signal": "neutral", "final_score": 50.0, "confidence": 50.0},
        meta_row=None,
        vol_penalty=5.0,
    )
    assert out["combined_vol"] is None
    assert out["components"]["vol_w"] == 1.0
    assert out["recommendation_score"] == 25.0  # 100 * 0.5 * 0.5 * 1.0


def test_out_of_range_inputs_are_clamped_to_valid_score():
    """상류 CHECK 가 깨진 비정상 입력(>100, 음수 vol)이 와도 점수는 0..100 안에 갇힌다."""
    out = score_recommendation(
        final_row={"signal": "positive", "final_score": 150.0, "confidence": 130.0},
        meta_row={"combined_vol": -0.5, "confidence": 2.0},
        vol_penalty=5.0,
    )
    assert out["components"]["dir_w"] == 1.0   # 150/100 → clamp 1.0
    assert out["components"]["conf_w"] == 1.0  # 130/100 → clamp 1.0
    assert out["components"]["vol_w"] == 1.0   # 음수 vol → 0 바닥 → 1/(1+0)=1
    assert 0.0 <= out["recommendation_score"] <= 100.0
    assert out["recommendation_score"] == 100.0


def test_none_numeric_fields_do_not_crash():
    """final_score/confidence 가 None 이어도 TypeError 없이 안전 기본값으로 처리."""
    out = score_recommendation(
        final_row={"signal": "neutral", "final_score": None, "confidence": None},
        meta_row=None,
        vol_penalty=5.0,
    )
    assert out["recommendation_score"] == 0.0  # conf None→0 → score 0
    assert out["components"]["dir_w"] == 0.5   # final_score None→중립 0.5
