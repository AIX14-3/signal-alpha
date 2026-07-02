"""저널 outcome 확정 로직 — 수집 DB(ohlcv_data) → 백엔드 DB(signal_journal_outcomes).

저널 작성 후 "실제 주가가 어떻게 됐나"를 거래일 기준 horizon(7td/30td)별로 1회 확정한다.
거래일 캘린더를 따로 두지 않고 **해당 종목의 ohlcv trade_date 시계열 자체**를 캘린더로
쓴다(휴장·거래정지 자동 처리). base = 작성일(KST) 이하 가장 최근 거래일 종가, target =
base 이후 N번째 거래일 종가. target 이 아직 없으면 skip → 다음 실행에서 재시도된다.

signal_publisher 와 같은 순수 SQL·연결 주입형(backend=백엔드, source=수집)이라 fake 로
단위테스트 가능하다. 백엔드 쓰기는 signal_journal_outcomes 테이블 한정 grant 계약
(20260702_1400) — 저널 원본(user_memo)은 컬럼 한정 SELECT 만 허용된다.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Sequence
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

# (horizon 라벨, 거래일 수). 라벨은 signal_journal_outcomes.horizon CHECK 와 일치해야 한다.
HORIZONS: tuple[tuple[str, int], ...] = (("7td", 7), ("30td", 30))

# base 거래일 탐색 여유 — 작성일 직전 연휴/거래정지를 감안해 시계열을 며칠 전부터 읽는다.
_BASE_LOOKBACK_DAYS = 45

_KST = ZoneInfo("Asia/Seoul")

_PENDING_SQL = """
SELECT j.id AS journal_id, j.stock_id, j.created_at
FROM signal_journals j
WHERE NOT EXISTS (
    SELECT 1
    FROM signal_journal_outcomes o
    WHERE o.journal_id = j.id
      AND o.horizon = $1
)
ORDER BY j.id
"""

_SERIES_SQL = """
SELECT trade_date, close
FROM ohlcv_data
WHERE stock_id = $1
  AND trade_date >= $2
ORDER BY trade_date ASC
"""

_UPSERT_SQL = """
INSERT INTO signal_journal_outcomes (
    journal_id, horizon, base_trade_date, base_price,
    outcome_trade_date, outcome_price, change_pct
)
VALUES ($1, $2, $3, $4, $5, $6, $7)
ON CONFLICT (journal_id, horizon) DO UPDATE SET
    base_trade_date = EXCLUDED.base_trade_date,
    base_price = EXCLUDED.base_price,
    outcome_trade_date = EXCLUDED.outcome_trade_date,
    outcome_price = EXCLUDED.outcome_price,
    change_pct = EXCLUDED.change_pct,
    checked_at = NOW()
"""


@dataclass
class OutcomeStats:
    stored: int = 0
    pending: int = 0  # target 거래일 미도래 — 다음 실행에서 재시도
    skipped: int = 0  # base 산정 불가(작성일 이전 시세 없음 등)
    failed: int = 0
    errors: list[str] = field(default_factory=list)


def resolve_outcome(
    series: Sequence[tuple[date, Any]],
    *,
    created_date: date,
    trading_days: int,
) -> dict[str, Any] | str:
    """시계열에서 base/target 거래일 종가를 골라 outcome 을 계산한다.

    반환: 확정 dict / ``"pending"``(target 미도래) / ``"skipped"``(base 불가).
    """
    base_index = None
    for index, (trade_date, _close) in enumerate(series):
        if trade_date <= created_date:
            base_index = index
        else:
            break
    if base_index is None:
        return "skipped"
    base_date, base_close = series[base_index]
    if not base_close:
        return "skipped"
    target_index = base_index + trading_days
    if target_index >= len(series):
        return "pending"
    target_date, target_close = series[target_index]
    change_pct = round((float(target_close) - float(base_close)) / float(base_close) * 100, 2)
    return {
        "base_trade_date": base_date,
        "base_price": base_close,
        "outcome_trade_date": target_date,
        "outcome_price": target_close,
        "change_pct": change_pct,
    }


async def record_journal_outcomes(
    backend_conn: Any,
    source_conn: Any,
    *,
    horizons: tuple[tuple[str, int], ...] = HORIZONS,
) -> OutcomeStats:
    """미확정 저널 전부에 대해 horizon 별 outcome 을 멱등 upsert 한다."""
    stats = OutcomeStats()

    # horizon 별 미확정 대상 수집 → 종목별 시계열은 1회만 읽는다.
    targets: list[tuple[str, int, int, int, date]] = []  # (horizon, days, journal_id, stock_id, created_date)
    for horizon, days in horizons:
        for row in await backend_conn.fetch(_PENDING_SQL, horizon):
            created_date = _to_kst_date(row["created_at"])
            targets.append((horizon, days, int(row["journal_id"]), int(row["stock_id"]), created_date))
    if not targets:
        return stats

    series_by_stock: dict[int, list[tuple[date, Any]]] = {}
    earliest_by_stock: dict[int, date] = {}
    for _horizon, _days, _journal_id, stock_id, created_date in targets:
        current = earliest_by_stock.get(stock_id)
        if current is None or created_date < current:
            earliest_by_stock[stock_id] = created_date

    for stock_id, earliest in earliest_by_stock.items():
        rows = await source_conn.fetch(
            _SERIES_SQL, stock_id, earliest - timedelta(days=_BASE_LOOKBACK_DAYS)
        )
        series_by_stock[stock_id] = [(row["trade_date"], row["close"]) for row in rows]

    for horizon, days, journal_id, stock_id, created_date in targets:
        try:
            outcome = resolve_outcome(
                series_by_stock.get(stock_id, []),
                created_date=created_date,
                trading_days=days,
            )
            if outcome == "pending":
                stats.pending += 1
                continue
            if outcome == "skipped":
                stats.skipped += 1
                logger.info("journal=%s horizon=%s: base 거래일 산정 불가 — skip", journal_id, horizon)
                continue
            await backend_conn.execute(
                _UPSERT_SQL,
                journal_id,
                horizon,
                outcome["base_trade_date"],
                outcome["base_price"],
                outcome["outcome_trade_date"],
                outcome["outcome_price"],
                outcome["change_pct"],
            )
            stats.stored += 1
        except Exception as exc:  # noqa: BLE001 - 저널 1건 실패가 전체를 막지 않음
            stats.failed += 1
            stats.errors.append(f"journal={journal_id} horizon={horizon}: {exc}")
            logger.warning("outcome upsert failed for journal=%s horizon=%s: %s", journal_id, horizon, exc)
    return stats


def _to_kst_date(value: Any) -> date:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(_KST).date()
        return value.date()
    return value
