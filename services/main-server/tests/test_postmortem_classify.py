"""3분류 판정 — 관측신호 유무로 '실수 아님' 구분 (순수)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

from app.postmortem.analysis import RoundTrip
from app.postmortem.classify import classify_roundtrip, signals_in_window


def _trip(pnl, is_open=False):
    return RoundTrip(
        ticker="005930", opened_at=datetime(2026, 6, 1),
        closed_at=None if is_open else datetime(2026, 6, 30),
        quantity=Decimal("10"), avg_buy_price=Decimal("100"),
        avg_sell_price=None if is_open else Decimal("78"),
        realized_pnl_pct=None if is_open else pnl,
        holding_days=None if is_open else 29, is_open=is_open,
    )


def _sig(day, kind="insider_sell"):
    return {"signal_date": date(2026, 6, day), "kind": kind}


def test_signals_in_window_filters_by_holding_period():
    sigs = [_sig(5), _sig(15), _sig(1)]  # 6/1 개시 이전 없음, 모두 창 안(6/1~6/30)
    got = signals_in_window(sigs, datetime(2026, 6, 3), datetime(2026, 6, 20))
    # 6/3~6/20 창 → 6/5, 6/15 만
    assert {s["signal_date"].day for s in got} == {5, 15}


def test_signals_in_window_uses_kst_date_for_aware_boundaries():
    # 개시 = 2026-06-01 23:00 UTC = 2026-06-02 08:00 KST → 창 시작은 KST 6/2.
    opened = datetime(2026, 6, 1, 23, 0, tzinfo=timezone.utc)
    got = signals_in_window([_sig(1), _sig(2)], opened, None)
    # 6/1 공시는 KST 거래일(6/2) 이전이라 제외, 6/2 만 포함.
    assert {s["signal_date"].day for s in got} == {2}


def test_observable_signal_when_insider_sell_in_window():
    trip = _trip(-22.0)  # 손절 이탈급 손실
    out = classify_roundtrip(trip, {"stop_violated": True}, [_sig(10)])
    assert out["verdict"] == "observable_signal"
    assert out["not_a_mistake"] is False and out["insider_sell_count"] == 1


def test_hindsight_only_when_no_signal():
    trip = _trip(-22.0)
    out = classify_roundtrip(trip, {"stop_violated": True}, [])
    assert out["verdict"] == "hindsight_only" and out["not_a_mistake"] is True


def test_insider_buy_does_not_count_as_exit_signal():
    trip = _trip(-15.0)
    out = classify_roundtrip(trip, {}, [_sig(10, kind="insider_buy")])
    # 큰 손실(-15<-10)이지만 매도신호 없음 → 착시
    assert out["verdict"] == "hindsight_only"


def test_on_plan_or_ok_when_not_underperformed():
    trip = _trip(8.0)  # 이익, 손절 이탈 없음
    out = classify_roundtrip(trip, {"stop_violated": False}, [_sig(10)])
    assert out["verdict"] == "on_plan_or_ok"


def test_open_trip_not_classified():
    out = classify_roundtrip(_trip(0, is_open=True), {}, [_sig(10)])
    assert out["verdict"] == "open"


def test_loss_threshold_triggers_without_plan():
    # 계획 없어도(plan_vs_actual 비어도) -10% 이하면 부검 대상.
    trip = _trip(-12.0)
    assert classify_roundtrip(trip, {}, [_sig(10)])["verdict"] == "observable_signal"
    trip2 = _trip(-5.0)  # -5% → 부진 아님
    assert classify_roundtrip(trip2, {}, [_sig(10)])["verdict"] == "on_plan_or_ok"
