"""Helpers for turning raw Kiwoom TR string fields into typed values.

Kiwoom returns every field as a string, often with thousands separators,
padding spaces, and a leading ``+``/``-`` sign that encodes the direction of
change rather than a true negative magnitude (e.g. prices). The helpers below
normalize those quirks.
"""

from datetime import date, datetime


def _clean(raw: object) -> str:
    if raw is None:
        return ""
    return str(raw).strip().replace(",", "").replace(" ", "")


def parse_int(raw: object, default: int = 0) -> int:
    """Parse a signed integer, preserving sign (used for net-flow values)."""
    text = _clean(raw)
    if text in ("", "+", "-"):
        return default
    try:
        return int(float(text))
    except ValueError:
        return default


def parse_price(raw: object, default: int = 0) -> int:
    """Parse a price magnitude.

    Kiwoom prefixes chart prices with a sign showing the move vs. the previous
    close; the magnitude is the real price, so we drop the sign.
    """
    return abs(parse_int(raw, default))


def parse_decimal(raw: object, default: float | None = None) -> float | None:
    """Parse a ratio/percentage field (PER, ROE, 등락률 …)."""
    text = _clean(raw)
    if text in ("", "+", "-"):
        return default
    try:
        return float(text)
    except ValueError:
        return default


def parse_points(raw: object, default: float = 0.0) -> float:
    """Parse an index level magnitude (업종지수, pt) dropping the direction sign."""
    value = parse_decimal(raw, None)
    return abs(value) if value is not None else default


def parse_date(raw: object, default: date | None = None) -> date | None:
    """Parse a ``YYYYMMDD`` trade date."""
    text = _clean(raw)
    if len(text) < 8:
        return default
    try:
        return datetime.strptime(text[:8], "%Y%m%d").date()
    except ValueError:
        return default


def field(record: dict[str, str], *candidates: str) -> str:
    """Return the first present field value among the candidate column names.

    Kiwoom column labels vary slightly across SDK versions (e.g. 상장주수 vs
    상장주식수), so collectors pass every known alias.
    """
    for name in candidates:
        if name in record and _clean(record[name]) != "":
            return record[name]
    return ""
