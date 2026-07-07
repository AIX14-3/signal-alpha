"""매매 부검 PIT 관측신호 오버레이 갱신 — 수집 DB(dart_ownership) → backend(overlays).

유저가 거래한 종목·구간에 대해 **그 시점 관측 가능했던(PIT)** 내부자/최대주주 공시를 수집
DB 에서 읽어 backend 오버레이에 멱등 적재한다. signal_journal_outcomes 워커와 같은 이중풀
계약(수집=읽기, backend=쓰기). report_date(공시 접수일)=known_at 이라 사후확신이 섞이지 않는다.

진입 이전 신호(살 시기 판단 근거)도 잡기 위해 거래 시작일보다 lookback 만큼 앞부터 읽는다.
연결·리포지토리 주입형이라 faked 로 단위테스트한다.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from signal_alpha_data_access.repositories.user_trade_signal_overlays import (
    UserTradeSignalOverlayRepository,
)

logger = logging.getLogger(__name__)

# 진입 이전 관측 신호도 포함하는 lookback.
_PRE_ENTRY_LOOKBACK_DAYS = 30

# PIT: report_date(공시 접수일) 이하 = 관측 가능 시점. 순증감 있는 건만(0 제외).
_DART_SQL = """
SELECT report_date, holder_type, holder_name, shares_delta, ratio_delta
FROM dart_ownership_events
WHERE stock_id = $1
  AND report_date >= $2
  AND report_date <= $3
  AND shares_delta IS NOT NULL
  AND shares_delta <> 0
ORDER BY report_date
"""


@dataclass
class OverlayStats:
    stocks: int = 0
    signals: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


async def refresh_signal_overlays(backend_conn: Any, source_conn: Any) -> OverlayStats:
    """유저 거래 종목별로 PIT 내부자 공시를 오버레이에 멱등 적재한다.

    종목 1개 실패가 전체를 막지 않는다(journal_outcomes 격리 철학).
    """
    repo = UserTradeSignalOverlayRepository(backend_conn)
    stats = OverlayStats()

    for row in await repo.traded_stock_ranges():
        try:
            start = row["start_date"] - timedelta(days=_PRE_ENTRY_LOOKBACK_DAYS)
            events = await source_conn.fetch(_DART_SQL, row["stock_id"], start, row["end_date"])
            for event in events:
                delta = event["shares_delta"]
                kind = "insider_sell" if delta < 0 else "insider_buy"
                detail = json.dumps(
                    {
                        "holder_type": event["holder_type"],
                        "holder_name": event["holder_name"],
                        "shares_delta": str(delta),
                        "ratio_delta": (
                            str(event["ratio_delta"]) if event["ratio_delta"] is not None else None
                        ),
                    },
                    ensure_ascii=False,
                )
                await repo.upsert_overlay(
                    user_id=int(row["user_id"]),
                    stock_id=int(row["stock_id"]),
                    ticker=row["ticker"],
                    signal_date=event["report_date"],
                    kind=kind,
                    detail=detail,
                )
                stats.signals += 1
            stats.stocks += 1
        except Exception as exc:  # noqa: BLE001 - 종목 1개 실패 격리
            stats.failed += 1
            stats.errors.append(f"stock={row['stock_id']}: {exc}")
            logger.warning("overlay refresh failed for stock=%s: %s", row["stock_id"], exc)

    return stats
