"""Numeric parsing helpers for Kiwoom REST responses.

Kiwoom returns numbers as strings, often with a leading +/- that encodes the
direction versus the previous close (e.g. "+74300", "-1.21") and sometimes
with thousands separators. Price/size fields want the magnitude; net-buy
fields keep their sign.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation


def _clean(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text or text in {"-", "+"}:
        return None
    return text


def parse_price(value: object) -> Decimal | None:
    """Magnitude of a price-like field; the +/- prefix is direction, not sign."""
    text = _clean(value)
    if text is None:
        return None
    try:
        return abs(Decimal(text))
    except InvalidOperation:
        return None


def parse_decimal(value: object) -> Decimal | None:
    """Signed decimal (e.g. change_pct, ROE)."""
    text = _clean(value)
    if text is None:
        return None
    try:
        return Decimal(text)
    except InvalidOperation:
        return None


def parse_int(value: object) -> int | None:
    """Magnitude of a count-like field (volume, market cap)."""
    parsed = parse_price(value)
    return int(parsed) if parsed is not None else None


def parse_signed_int(value: object) -> int | None:
    """Signed integer (net buy quantities keep their sign)."""
    parsed = parse_decimal(value)
    return int(parsed) if parsed is not None else None
