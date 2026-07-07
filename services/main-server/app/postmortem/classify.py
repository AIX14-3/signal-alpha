"""3분류 판정 — 관측 가능 신호 유무로 "실수 아님"을 구분(사후확신 금지).

부진했던 라운드트립(계획 손절 이탈 or 큰 손실)에 대해, 보유 구간에 **그 시점 관측 가능했던**
PIT 신호(내부자 매도 등)가 있었는지로 판정한다:
- 신호 있었음 → ① 몰랐다 / ② 봤지만 안 함 (유저에게 물음). "그때 볼 수 있었다".
- 신호 없었음 → ③ 착시 = **실수 아님**(사후 고점만 아는 것). 이게 차별화 포인트.

순수 함수(입력 = 트립·plan-vs-actual·구간 신호). 사후 고점/저점은 판정에 쓰지 않는다.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from zoneinfo import ZoneInfo

# dart report_date(공시 접수일)는 한국 기준. 체결(timestamptz)도 KST 거래일 기준으로 맞춰야
# 보유 구간 경계가 report_date 와 같은 달력으로 비교된다(UTC .date() 로 하루 어긋남 방지).
_KST = ZoneInfo("Asia/Seoul")

# 계획 손절 이탈이 없어도 이만큼 손실이면 부검 대상(무계획 손실도 판정).
_LOSS_THRESHOLD_PCT = -10.0


def signals_in_window(
    signals: list[dict[str, Any]], opened_at: Any, closed_at: Any
) -> list[dict[str, Any]]:
    """오버레이 신호를 라운드트립 보유 구간 [개시, 청산]으로 필터. signal_date=관측시점."""
    start = _as_date(opened_at)
    end = _as_date(closed_at)
    out = []
    for s in signals:
        d = _as_date(s.get("signal_date"))
        if d is None or start is None:
            continue
        if d >= start and (end is None or d <= end):
            out.append(s)
    return out


def classify_roundtrip(
    trip: Any, plan_vs_actual: dict[str, Any], window_signals: list[dict[str, Any]]
) -> dict[str, Any]:
    """라운드트립 1건 3분류 판정."""
    if trip.is_open:
        return {"verdict": "open"}
    pnl = trip.realized_pnl_pct
    stop_violated = plan_vs_actual.get("stop_violated") is True
    underperformed = stop_violated or (pnl is not None and pnl <= _LOSS_THRESHOLD_PCT)
    if not underperformed:
        return {"verdict": "on_plan_or_ok"}
    insider_sells = [s for s in window_signals if s.get("kind") == "insider_sell"]
    if insider_sells:
        # ①/② — 관측 가능한 신호가 있었다. 훈수 아님(사실).
        return {
            "verdict": "observable_signal",
            "not_a_mistake": False,
            "insider_sell_count": len(insider_sells),
        }
    # ③ — 그 시점엔 관측 가능한 신호 없음. 사후 고점만 아는 것 = 실수 아님.
    return {"verdict": "hindsight_only", "not_a_mistake": True}


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        # tz-aware(체결 timestamptz)는 KST 거래일로, naive 는 그대로.
        if value.tzinfo is not None:
            return value.astimezone(_KST).date()
        return value.date()
    if isinstance(value, date):
        return value
    return None
