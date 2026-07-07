"""매매 부검 순수 계산 — 라운드트립·Plan vs Actual·패턴."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from app.postmortem.analysis import (
    Fill,
    RoundTrip,
    analyze_patterns,
    analyze_plan_vs_actual,
    build_round_trips,
)


def _f(side, day, qty, price):
    return Fill(side=side, filled_at=datetime(2026, 7, day), quantity=Decimal(qty), price=Decimal(price))


def test_simple_round_trip_pnl_and_holding():
    trips = build_round_trips("005930", [_f("buy", 1, "10", "70000"), _f("sell", 8, "10", "77000")])
    assert len(trips) == 1
    t = trips[0]
    assert not t.is_open
    assert t.avg_buy_price == Decimal("70000") and t.avg_sell_price == Decimal("77000")
    assert t.realized_pnl_pct == 10.0
    assert t.holding_days == 7


def test_average_cost_across_adds_and_partial_sells():
    # 매수 10@100, 10@200 → 평균 150. 전량 20 매도@180 → +20%.
    trips = build_round_trips(
        "X", [_f("buy", 1, "10", "100"), _f("buy", 2, "10", "200"), _f("sell", 5, "20", "180")]
    )
    assert len(trips) == 1
    assert trips[0].avg_buy_price == Decimal("150")
    assert trips[0].realized_pnl_pct == 20.0


def test_partial_sell_then_close_is_one_trip():
    trips = build_round_trips(
        "X", [_f("buy", 1, "10", "100"), _f("sell", 3, "4", "110"), _f("sell", 5, "6", "120")]
    )
    assert len(trips) == 1 and not trips[0].is_open
    # 평균 매도 = (4*110 + 6*120)/10 = 116 → +16%
    assert trips[0].avg_sell_price == Decimal("116")
    assert trips[0].realized_pnl_pct == 16.0


def test_reentry_makes_two_trips():
    trips = build_round_trips(
        "X",
        [_f("buy", 1, "10", "100"), _f("sell", 2, "10", "110"),
         _f("buy", 5, "10", "120"), _f("sell", 9, "10", "108")],
    )
    assert len(trips) == 2
    assert trips[0].realized_pnl_pct == 10.0
    assert trips[1].realized_pnl_pct == -10.0


def test_open_position_is_flagged_not_closed():
    trips = build_round_trips("X", [_f("buy", 1, "10", "100"), _f("sell", 2, "4", "110")])
    assert len(trips) == 1 and trips[0].is_open
    assert trips[0].avg_sell_price is None and trips[0].realized_pnl_pct is None


def test_sell_without_position_ignored():
    trips = build_round_trips("X", [_f("sell", 1, "10", "100"), _f("buy", 2, "10", "100")])
    # 첫 매도는 보유 없음 → 무시, 이후 매수만 → 미청산 1건.
    assert len(trips) == 1 and trips[0].is_open


def _trip(pnl, hold, is_open=False):
    # closed_at 은 patterns 계산에 쓰이지 않는다(holding_days 사용) — 고정 유효일자.
    return RoundTrip(
        ticker="X", opened_at=datetime(2026, 7, 1),
        closed_at=None if is_open else datetime(2026, 8, 1),
        quantity=Decimal("1"), avg_buy_price=Decimal("100"),
        avg_sell_price=None if is_open else Decimal("110"),
        realized_pnl_pct=None if is_open else pnl,
        holding_days=None if is_open else hold, is_open=is_open,
    )


def test_plan_vs_actual_stop_violation():
    trip = _trip(-22.0, 30)  # 실제 -22%
    plan = {"stop_price": 93, "target_price": 120, "thesis": "t"}  # buy 100 → stop -7%, target +20%
    out = analyze_plan_vs_actual(trip, plan)
    assert out["has_plan"] and out["evaluated"]
    assert out["planned_stop_pct"] == -7.0
    assert out["stop_violated"] is True  # -22% < -7%
    assert out["reached_target"] is False


def test_plan_vs_actual_no_plan():
    assert analyze_plan_vs_actual(_trip(5.0, 3), None) == {"has_plan": False}


def test_plan_vs_actual_open_trip_not_evaluated():
    out = analyze_plan_vs_actual(_trip(0, 0, is_open=True), {"stop_price": 90})
    assert out["has_plan"] and out["evaluated"] is False


def test_patterns_disposition_effect():
    # 손실 트립을 이익 트립보다 오래 보유 → 처분효과.
    trips = [_trip(10, 3), _trip(15, 4), _trip(-8, 30), _trip(-12, 40), _trip(5, 2)]
    p = analyze_patterns(trips)
    assert p["sample"] == 5
    assert p["win_rate"] == 0.6
    assert p["avg_hold_loss_days"] > p["avg_hold_win_days"]
    assert p["disposition_effect"] is True


def test_patterns_empty():
    assert analyze_patterns([]) == {"sample": 0}
    assert analyze_patterns([_trip(0, 0, is_open=True)]) == {"sample": 0}
