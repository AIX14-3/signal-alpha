"""매매 의사결정 부검 — 순수 계산(라운드트립·Plan vs Actual·패턴).

체결(fill) 목록에서 예측·사후확신 없이 **사실만** 뽑는다:
- 라운드트립(포지션 개시~전량청산)을 평균단가법으로 묶는다.
- Plan vs Actual: 유저 자기 계획(목표가·손절가)과 실제 청산가를 대조(시장 look-ahead 없음).
- 패턴: 처분효과(손실 오래 보유) 등 라운드트립 집계.

모두 순수 함수(입력 = 체결/계획, 외부·시각 의존 없음)라 단위테스트로 완전 검증된다.
FOMO 진입·조급 익절 등 **가격 경로가 필요한 패턴**은 시세 오버레이가 붙는 후속(PR-3b)에서.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any


@dataclass(frozen=True)
class Fill:
    side: str  # 'buy' | 'sell'
    filled_at: datetime
    quantity: Decimal
    price: Decimal
    fee: Decimal | None = None


@dataclass
class RoundTrip:
    ticker: str
    opened_at: datetime
    closed_at: datetime | None
    quantity: Decimal  # 이 트립에서 매수한 총 수량
    avg_buy_price: Decimal
    avg_sell_price: Decimal | None
    realized_pnl_pct: float | None
    holding_days: int | None
    is_open: bool


def build_round_trips(ticker: str, fills: list[Fill]) -> list[RoundTrip]:
    """체결을 평균단가법으로 라운드트립(개시~전량청산)으로 묶는다.

    포지션이 0→양수면 개시, 양수→0이면 청산(1 트립 확정). 부분 매수/매도는 평균단가로 흡수.
    보유 없는 매도(pos<=0)는 무시(공매도 미모델, 롱 전용 가정). 마지막에 포지션이 남아 있으면
    미청산 트립(is_open=True)으로 emit.

    같은 체결시각(수기 입력은 날짜만 받아 당일 매매가 동일 timestamp)일 때는 매수를 매도보다
    먼저 처리한다 — 롱 전용에서 매도는 반드시 선행 매수가 있어야 하므로, 이 타이브레이크가
    없으면 당일 매수·매도가 매도-선행 순서로 들어와 청산 라운드트립이 미청산으로 오분류된다.
    """
    ordered = sorted(fills, key=lambda f: (f.filled_at, 0 if f.side == "buy" else 1))
    trips: list[RoundTrip] = []

    pos_qty = Decimal(0)
    buy_qty = Decimal(0)
    buy_cost = Decimal(0)
    sell_qty = Decimal(0)
    sell_proceeds = Decimal(0)
    opened_at: datetime | None = None

    def _reset() -> None:
        nonlocal buy_qty, buy_cost, sell_qty, sell_proceeds, opened_at
        buy_qty = Decimal(0)
        buy_cost = Decimal(0)
        sell_qty = Decimal(0)
        sell_proceeds = Decimal(0)
        opened_at = None

    for f in ordered:
        if f.side == "buy":
            if pos_qty == 0:
                opened_at = f.filled_at
            pos_qty += f.quantity
            buy_qty += f.quantity
            buy_cost += f.quantity * f.price
        elif f.side == "sell":
            if pos_qty <= 0:
                continue  # 보유 없는 매도 무시
            filled = min(f.quantity, pos_qty)
            pos_qty -= filled
            sell_qty += filled
            sell_proceeds += filled * f.price
            if pos_qty == 0 and opened_at is not None and buy_qty > 0:
                avg_buy = buy_cost / buy_qty
                avg_sell = sell_proceeds / sell_qty
                pnl = float((avg_sell - avg_buy) / avg_buy * 100)
                trips.append(
                    RoundTrip(
                        ticker=ticker,
                        opened_at=opened_at,
                        closed_at=f.filled_at,
                        quantity=buy_qty,
                        avg_buy_price=avg_buy,
                        avg_sell_price=avg_sell,
                        realized_pnl_pct=round(pnl, 2),
                        holding_days=(f.filled_at - opened_at).days,
                        is_open=False,
                    )
                )
                _reset()

    if pos_qty > 0 and opened_at is not None and buy_qty > 0:
        trips.append(
            RoundTrip(
                ticker=ticker,
                opened_at=opened_at,
                closed_at=None,
                quantity=buy_qty,
                avg_buy_price=buy_cost / buy_qty,
                avg_sell_price=None,
                realized_pnl_pct=None,
                holding_days=None,
                is_open=True,
            )
        )
    return trips


def analyze_plan_vs_actual(trip: RoundTrip, plan: dict[str, Any] | None) -> dict[str, Any]:
    """유저 계획(목표가·손절가) 대비 실제 청산을 대조. 시장 look-ahead 없음(유저 규칙 대비만).

    계획 미기록 시 has_plan=False → "계획 없음 → 놓칠 수밖에 없는 구조"(FR-4). 미청산 트립은
    비교 불가(청산가 없음).
    """
    if plan is None:
        return {"has_plan": False}
    out: dict[str, Any] = {"has_plan": True, "thesis": plan.get("thesis") or None}
    if trip.is_open or trip.realized_pnl_pct is None:
        out["evaluated"] = False
        return out
    out["evaluated"] = True
    avg_buy = trip.avg_buy_price
    stop = _dec(plan.get("stop_price"))
    target = _dec(plan.get("target_price"))
    if stop is not None and avg_buy > 0:
        planned_stop_pct = round(float((stop - avg_buy) / avg_buy * 100), 2)
        out["planned_stop_pct"] = planned_stop_pct
        # 실제 청산이 손절선보다 더 나빴는가(계획 이탈 = 처분효과 신호).
        out["stop_violated"] = trip.realized_pnl_pct < planned_stop_pct
    if target is not None and avg_buy > 0:
        planned_target_pct = round(float((target - avg_buy) / avg_buy * 100), 2)
        out["planned_target_pct"] = planned_target_pct
        out["reached_target"] = trip.realized_pnl_pct >= planned_target_pct
    out["sell_condition"] = plan.get("sell_condition") or None
    return out


def analyze_patterns(trips: list[RoundTrip]) -> dict[str, Any]:
    """청산된 라운드트립 집계 — 처분효과·승률·평균손익(순수 집계, 예측 0).

    소표본 경계는 호출부(라우트)가 sample 로 판단해 억제한다(spec §7 임계).
    """
    closed = [t for t in trips if not t.is_open and t.realized_pnl_pct is not None]
    if not closed:
        return {"sample": 0}
    winners = [t for t in closed if (t.realized_pnl_pct or 0) > 0]
    losers = [t for t in closed if (t.realized_pnl_pct or 0) <= 0]
    avg_hold_win = _avg([t.holding_days for t in winners])
    avg_hold_loss = _avg([t.holding_days for t in losers])
    return {
        "sample": len(closed),
        "win_rate": round(len(winners) / len(closed), 3),
        "avg_win_pct": _avg([t.realized_pnl_pct for t in winners]),
        "avg_loss_pct": _avg([t.realized_pnl_pct for t in losers]),
        "avg_hold_win_days": avg_hold_win,
        "avg_hold_loss_days": avg_hold_loss,
        # 처분효과: 손실 종목을 이익 종목보다 오래 보유(전형적 실수).
        "disposition_effect": (
            avg_hold_win is not None and avg_hold_loss is not None and avg_hold_loss > avg_hold_win
        ),
    }


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    try:
        # 콤마 포함 문자열("95,000")도 허용 — 없으면 손절 판정이 조용히 스킵된다.
        return Decimal(str(value).replace(",", "").strip())
    except (ValueError, ArithmeticError):
        return None


def _avg(values: list[Any]) -> float | None:
    nums = [float(v) for v in values if v is not None]
    return round(sum(nums) / len(nums), 2) if nums else None
