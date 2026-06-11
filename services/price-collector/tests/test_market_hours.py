from datetime import datetime, time

from app.core.market_hours import (
    KST,
    is_market_open,
    next_market_open,
    parse_hhmm,
)

OPEN = time(9, 0)
CLOSE = time(15, 30)


def kst(year, month, day, hour, minute):
    return datetime(year, month, day, hour, minute, tzinfo=KST)


def test_parse_hhmm():
    assert parse_hhmm("09:00") == time(9, 0)
    assert parse_hhmm("15:30") == time(15, 30)


def test_open_during_weekday_session():
    assert is_market_open(kst(2026, 6, 11, 9, 0), OPEN, CLOSE)  # Thursday open
    assert is_market_open(kst(2026, 6, 11, 15, 30), OPEN, CLOSE)
    assert is_market_open(kst(2026, 6, 11, 12, 0), OPEN, CLOSE)


def test_closed_outside_session():
    assert not is_market_open(kst(2026, 6, 11, 8, 59), OPEN, CLOSE)
    assert not is_market_open(kst(2026, 6, 11, 15, 31), OPEN, CLOSE)


def test_closed_on_weekend():
    assert not is_market_open(kst(2026, 6, 13, 10, 0), OPEN, CLOSE)  # Saturday
    assert not is_market_open(kst(2026, 6, 14, 10, 0), OPEN, CLOSE)  # Sunday


def test_next_open_same_day_before_open():
    assert next_market_open(kst(2026, 6, 11, 7, 0), OPEN) == kst(2026, 6, 11, 9, 0)


def test_next_open_rolls_past_weekend():
    # Friday after close -> Monday 09:00
    assert next_market_open(kst(2026, 6, 12, 16, 0), OPEN) == kst(2026, 6, 15, 9, 0)
    # Saturday -> Monday 09:00
    assert next_market_open(kst(2026, 6, 13, 10, 0), OPEN) == kst(2026, 6, 15, 9, 0)
