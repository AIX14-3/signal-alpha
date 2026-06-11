"""KST market-session helpers for the polling loop.

Only weekday/session-time gating is applied here. Korean market holidays are
not modelled; on a holiday the poll simply collects unchanged snapshots.
"""

from __future__ import annotations

from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")


def parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def is_trading_day(moment: datetime) -> bool:
    return moment.weekday() < 5


def is_market_open(moment: datetime, open_time: time, close_time: time) -> bool:
    if not is_trading_day(moment):
        return False
    return open_time <= moment.time() <= close_time


def next_market_open(moment: datetime, open_time: time) -> datetime:
    candidate = moment.replace(
        hour=open_time.hour, minute=open_time.minute, second=0, microsecond=0
    )
    if moment.time() >= open_time or not is_trading_day(moment):
        candidate += timedelta(days=1)
    while not is_trading_day(candidate):
        candidate += timedelta(days=1)
    return candidate
