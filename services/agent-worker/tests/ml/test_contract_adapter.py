"""Adapter: OHLCV rows → DataContract, and parity/no-look-ahead for EWMA.

These tests are pure (no DB): they feed the same row-dict shape the PRICE
collector emits and assert the canonical frame, point-in-time slicing, and that
the vendored EWMA baseline gets byte-identical input through the adapter.
"""

from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

from app.ml.contract_adapter import build_contract, build_frame
from vol_models.models import cpu_ewma

_START = date(2026, 1, 1)


def _rows(closes: list[float]) -> list[dict]:
    """Synthetic ascending OHLCV rows with the keys OhlcvReader emits."""
    rows = []
    for i, close in enumerate(closes):
        rows.append(
            {
                "trade_date": (_START + timedelta(days=i)).isoformat(),
                "open": close * 0.99,
                "high": close * 1.02,
                "low": close * 0.98,
                "close": close,
                "volume": 1000 + i,
            }
        )
    return rows


def test_build_frame_canonical_columns_and_sorted() -> None:
    rows = list(reversed(_rows([100, 101, 102, 103])))  # feed out of order
    frame = build_frame(rows, ticker="005930")

    assert list(frame.columns) == [
        "date", "ticker", "open", "high", "low", "close", "volume", "ret", "rv_d",
    ]
    # sorted ascending by date despite reversed input
    assert list(frame["date"]) == sorted(frame["date"])
    assert (frame["ticker"] == "005930").all()
    # first log return is NaN; the rest are finite
    assert math.isnan(frame["ret"].iloc[0])
    assert frame["ret"].iloc[1:].notna().all()
    # rv_d is a strictly positive variance proxy (clipped at 1e-12)
    assert (frame["rv_d"] > 0).all()


def test_build_frame_rejects_empty_and_missing_keys() -> None:
    with pytest.raises(ValueError):
        build_frame([], ticker="005930")
    with pytest.raises(ValueError):
        build_frame([{"trade_date": "2026-01-01", "close": 100}], ticker="005930")


def test_contract_returns_drop_first_nan_and_are_point_in_time() -> None:
    contract = build_contract(_rows([100, 101, 102, 103, 104]), ticker="005930")
    asof_idx = 2  # 3rd session
    returns = contract.returns(asof_idx)
    # 3 sessions ⇒ 2 finite returns (first NaN dropped), nothing from the future
    assert len(returns) == 2
    assert returns.notna().all()


def test_ewma_short_history_matches_closed_form() -> None:
    # < 25 returns ⇒ EWMA falls back to sqrt(var(returns) * horizon)
    contract = build_contract(_rows([100, 102, 101, 103, 105, 104]), ticker="005930")
    asof_idx = len(contract) - 1
    horizon = 10

    got = cpu_ewma.predict(contract, asof_idx, horizon, {}, np.random.default_rng(0))

    r = contract.returns(asof_idx).to_numpy()
    expected = math.sqrt(max(float(np.var(r)), 1e-12) * horizon)
    assert got == pytest.approx(expected)


def test_ewma_is_point_in_time_no_look_ahead() -> None:
    # A forecast at asof_idx must NOT depend on rows after asof: predicting on the
    # full history at asof_idx equals predicting on history truncated at asof_idx.
    rng = np.random.default_rng(42)
    closes = [100 + 5 * math.sin(i / 3) + i * 0.1 for i in range(40)]
    full = build_contract(_rows(closes), ticker="005930")
    asof_idx = 30
    horizon = 10

    pred_full = cpu_ewma.predict(full, asof_idx, horizon, {}, rng)

    truncated = build_contract(_rows(closes[: asof_idx + 1]), ticker="005930")
    pred_truncated = cpu_ewma.predict(truncated, asof_idx, horizon, {}, rng)

    assert pred_full == pytest.approx(pred_truncated)
