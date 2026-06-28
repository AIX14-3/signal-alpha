"""주가 BASE 학습 표본 생성(collect_price_samples) — DB 없이 fake 로 검증 (C안).

밀집 일별 패널: 각 asof 의 피처는 과거+당일만(PIT), 라벨은 미래 horizon 수익률, step 간격 표본.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from app.ml.source_features import PRICE_FEATURE_KEYS
from app.ml.train_price_model import collect_price_samples


class _FakeConn:
    def __init__(self, rows):
        self._rows = rows

    async def fetch(self, _sql):
        return self._rows


def _ohlcv(stock_id: int, closes: list[float], *, start: date = date(2026, 1, 1)) -> list[dict]:
    return [
        {
            "stock_id": stock_id,
            "trade_date": start + timedelta(days=i),
            "close": c,
            "volume": 1000,
            "foreign_net": 10,
            "institution_net": -5,
        }
        for i, c in enumerate(closes)
    ]


def test_collect_price_samples_label_and_pit():
    closes = [100.0 + i for i in range(90)]  # 단조 상승
    conn = _FakeConn(_ohlcv(7, closes))
    samples = asyncio.run(
        collect_price_samples(conn, horizon=20, min_history=65, step=5)
    )
    # asof 인덱스 i ∈ range(64, 70, 5) = [64, 69] → 2 표본.
    assert len(samples) == 2
    first = samples[0]
    # 라벨 = close[i+20]/close[i]-1 (i=64): (104+20... )/(164.. ) 실제값 검증.
    assert abs(first["label"] - (closes[84] / closes[64] - 1.0)) < 1e-12
    assert first["label"] > 0  # 단조 상승 → 양의 forward return
    # 피처는 스케일-프리 키셋, asof(2026-01-65일째) 이전만 반영(PIT) → 결측 없음.
    assert set(first["features"].keys()) == set(PRICE_FEATURE_KEYS)
    assert first["features"]["ret_20d"] is not None


def test_collect_price_samples_pools_multiple_stocks():
    rows = _ohlcv(1, [100.0 + i for i in range(90)]) + _ohlcv(2, [200.0 - i for i in range(90)])
    samples = asyncio.run(collect_price_samples(_FakeConn(rows), horizon=20, min_history=65, step=5))
    # 종목 2개 풀링 → 각 2 표본 = 4. 상승/하락 라벨 부호가 종목별로 갈린다.
    assert len(samples) == 4
    labels = [s["label"] for s in samples]
    assert any(v > 0 for v in labels) and any(v < 0 for v in labels)


def test_collect_price_samples_skips_when_insufficient_history():
    # min_history+horizon 보다 짧으면 표본 0.
    conn = _FakeConn(_ohlcv(1, [100.0 + i for i in range(50)]))
    samples = asyncio.run(collect_price_samples(conn, horizon=20, min_history=65, step=5))
    assert samples == []
