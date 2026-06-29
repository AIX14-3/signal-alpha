"""주가 BASE 피처(src_price) — 스케일-프리·PIT·결측 안전 검증 (C안 P1).

전 종목 패널 풀링(D1)이므로 절대가/절대거래량이 아니라 비율 피처만 써야 한다는 게 핵심 계약.
"""

from __future__ import annotations

from datetime import date, timedelta

from app.ml.source_features import (
    PRICE_FEATURE_KEYS,
    assemble_features,
    price_features,
)
from app.ml.source_models import SOURCE_MODELS, feature_order


def _series(closes: list[float], *, start: date = date(2026, 1, 1)) -> list[dict]:
    """closes 길이만큼 연속 거래일 OHLCV 행(거래량/수급 일정)."""
    return [
        {
            "trade_date": (start + timedelta(days=i)).isoformat(),
            "close": c,
            "volume": 1000,
            "foreign_net": 10,
            "institution_net": -5,
        }
        for i, c in enumerate(closes)
    ]


def test_empty_rows_return_all_none_keys():
    feats = price_features([])
    assert set(feats.keys()) == set(PRICE_FEATURE_KEYS)
    assert all(value is None for value in feats.values())


def test_feature_order_includes_price_and_is_sorted():
    order = feature_order("price")
    assert set(order) == set(PRICE_FEATURE_KEYS)
    assert order == sorted(order)
    assert "src_price" in SOURCE_MODELS and SOURCE_MODELS["src_price"] == "price"


def test_scale_invariance_same_relative_series():
    # 절대가가 10배 달라도 비율 피처(수익률·갭·RSI·streak)는 동일해야 한다(종목 풀링 핵심).
    closes = [100.0 + i for i in range(65)]
    low = price_features(_series(closes))
    high = price_features(_series([c * 10 for c in closes]))
    for key in ("ret_5d", "ret_20d", "close_sma20_gap", "sma5_sma20_gap", "sma20_sma60_gap", "rsi14"):
        assert low[key] is not None
        assert abs(low[key] - high[key]) < 1e-9, key


def test_return_and_gap_directionality():
    up = price_features(_series([100.0 + i for i in range(65)]))  # 단조 상승
    down = price_features(_series([200.0 - i for i in range(65)]))  # 단조 하락
    assert up["ret_20d"] > 0 and down["ret_20d"] < 0
    assert up["close_sma20_gap"] > 0 and down["close_sma20_gap"] < 0


def test_pit_gate_excludes_future_rows():
    # asof 이후 trade_date 행은 피처에서 제외(look-ahead 0).
    rows = _series([100.0 + i for i in range(65)])  # 2026-01-01 .. 2026-03-06
    asof = date(2026, 1, 20)
    feats = assemble_features(asof, price_rows=rows)["price"]
    # asof 까지 20세션뿐 → ret_20d(>20세션 필요)는 None, ret_5d 는 계산됨.
    assert feats["ret_5d"] is not None
    assert feats["ret_20d"] is None


def test_flow_ratio_is_scale_free_fraction():
    feats = price_features(_series([100.0 + i for i in range(65)]))
    # foreign_net=10, 20일 거래대금=avg_vol(1000)*20=20000 → 10*20/20000 = 0.01
    assert feats["foreign_flow_ratio20"] is not None
    assert abs(feats["foreign_flow_ratio20"] - (10 * 20) / (1000 * 20)) < 1e-9
