"""price 메타 피처 결정론 서술기 단위 테스트.

모델 없이도(현저성) 동작하고, contributions(pred_contrib) 가 주어지면 |기여도| 상위 피처가
앞에 오는지 검증한다. lightgbm 불필요(기여도 dict 직접 주입).
"""

from app.narrate.price_features import narrate_price_features

# 다양한 신호가 켜진 대표 피처셋.
_FEATS = {
    "ret_5d": 0.032,
    "ret_20d": 0.08,
    "close_sma20_gap": 0.04,
    "sma5_sma20_gap": 0.02,
    "sma20_sma60_gap": 0.015,
    "rsi14": 62,
    "volume_z": 2.4,
    "volatility20": 3.5,
    "golden_cross": 1.0,
    "dead_cross": 0.0,
    "foreign_streak": 4.0,
    "institution_streak": -3.0,
    "foreign_flow_ratio20": 0.012,
    "institution_flow_ratio20": -0.008,
}


def test_salience_ranking_without_model():
    n = narrate_price_features(features=_FEATS, contributions=None, direction="positive")
    assert n is not None
    assert "긍정" in n.summary
    assert "두드러진 지표" in n.summary  # 모델 없음 표기
    assert 1 <= len(n.key_facts) <= 4
    # 현저성이 높은 골든크로스가 상위에 포함된다.
    assert any("골든크로스" in f for f in n.key_facts)


def test_contribution_ranking_puts_top_contributor_first():
    # rsi14 에 가장 큰 |기여도| → 첫 번째 근거여야 한다(현저성만이면 뒤로 밀릴 값).
    contributions = {
        "rsi14": 0.95,
        "ret_5d": 0.02,
        "ret_20d": 0.02,
        "close_sma20_gap": 0.01,
        "sma5_sma20_gap": 0.01,
        "sma20_sma60_gap": 0.01,
        "volume_z": 0.01,
        "volatility20": 0.01,
        "golden_cross": 0.01,
        "foreign_streak": 0.01,
        "institution_streak": 0.01,
        "foreign_flow_ratio20": 0.01,
        "institution_flow_ratio20": 0.01,
    }
    n = narrate_price_features(
        features=_FEATS, contributions=contributions, direction="positive"
    )
    assert n is not None
    assert "모델이 가장 크게 반영한 지표" in n.summary
    assert "RSI" in n.key_facts[0]  # 최상위 기여 피처


def test_returns_none_when_no_notable_feature():
    assert narrate_price_features(features={"ret_5d": 0.001, "rsi14": 50}) is None


def test_handles_missing_and_none_values():
    n = narrate_price_features(
        features={"ret_5d": None, "rsi14": 75, "foreign_streak": 5.0}, direction="negative"
    )
    assert n is not None
    assert any("과매수" in f for f in n.key_facts)
    assert any("외국인" in f for f in n.key_facts)
