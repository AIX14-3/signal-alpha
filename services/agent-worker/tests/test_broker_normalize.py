"""브로커 응답 정규화 — 토스/키움 payload → NormalizedFill (순수·방어적)."""

from __future__ import annotations

from datetime import timezone
from decimal import Decimal

from app.collectors.broker.kiwoom_account import normalize_kiwoom_rows
from app.collectors.broker.toss_account import normalize_toss_orders


def test_toss_normalizes_execution():
    payload = {
        "orders": [
            {
                "orderId": "O1",
                "symbol": "005930",
                "side": "buy",
                "executions": [
                    {
                        "executionId": "E1",
                        "quantity": "10",
                        "price": "70000",
                        "executedAt": "2026-07-01T10:00:00+09:00",
                        "fee": "100",
                    }
                ],
            }
        ]
    }
    fills = normalize_toss_orders(payload)
    assert len(fills) == 1
    f = fills[0]
    assert f.broker_fill_id == "O1:E1"
    assert f.ticker == "005930" and f.side == "buy"
    assert f.quantity == Decimal("10") and f.price == Decimal("70000")
    assert f.fee == Decimal("100")


def test_toss_skips_incomplete_execution_and_unknown_side():
    payload = {
        "orders": [
            {"orderId": "O2", "symbol": "000660", "side": "buy",
             "executions": [{"executionId": "E9", "price": "1"}]},  # 수량 없음 → skip
            {"orderId": "O3", "symbol": "000660", "side": "hold",  # side 미상 → 주문 skip
             "executions": [{"executionId": "E10", "quantity": "1", "price": "1",
                             "executedAt": "2026-07-01T10:00:00+09:00"}]},
        ]
    }
    assert normalize_toss_orders(payload) == []


def test_kiwoom_strips_a_prefix_and_maps_side_and_ts():
    payload = {
        "acnt_ord_cntr_dtl": [
            {
                "cntr_no": "C1",
                "stk_cd": "A005930",
                "io_tp_nm": "2",  # 매수
                "cntr_qty": "5",
                "cntr_pric": "71000",
                "cntr_dt": "20260701",
                "cntr_tm": "100000",
                "cmsn": "50",
            }
        ]
    }
    fills = normalize_kiwoom_rows(payload)
    assert len(fills) == 1
    f = fills[0]
    assert f.ticker == "005930" and f.side == "buy"
    assert f.broker_fill_id == "C1"
    assert f.quantity == Decimal("5") and f.price == Decimal("71000")
    # 2026-07-01 10:00 KST == 01:00 UTC
    assert f.filled_at.astimezone(timezone.utc).hour == 1


def test_kiwoom_sell_token_and_skips_zero_qty():
    payload = {
        "acnt_ord_cntr_dtl": [
            {"cntr_no": "C2", "stk_cd": "000660", "io_tp_nm": "1", "cntr_qty": "3",
             "cntr_pric": "120000", "cntr_dt": "20260702", "cntr_tm": "133000"},
            {"cntr_no": "C3", "stk_cd": "000660", "io_tp_nm": "1", "cntr_qty": "0",  # 미체결 → skip
             "cntr_pric": "120000", "cntr_dt": "20260702", "cntr_tm": "133000"},
        ]
    }
    fills = normalize_kiwoom_rows(payload)
    assert len(fills) == 1 and fills[0].side == "sell" and fills[0].broker_fill_id == "C2"


def test_empty_payloads():
    assert normalize_toss_orders({}) == []
    assert normalize_kiwoom_rows({}) == []
