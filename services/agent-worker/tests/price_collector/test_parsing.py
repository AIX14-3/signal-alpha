from decimal import Decimal

from app.collectors.price.kiwoom.parsing import parse_decimal, parse_int, parse_price, parse_signed_int


def test_parse_price_strips_direction_prefix_and_commas():
    assert parse_price("+74,300") == Decimal("74300")
    assert parse_price("-73600") == Decimal("73600")
    assert parse_price("74300") == Decimal("74300")


def test_parse_price_empty_values():
    assert parse_price("") is None
    assert parse_price(None) is None
    assert parse_price("-") is None
    assert parse_price("abc") is None


def test_parse_decimal_keeps_sign():
    assert parse_decimal("-1.21") == Decimal("-1.21")
    assert parse_decimal("+9.81") == Decimal("9.81")


def test_parse_int_magnitude():
    assert parse_int("+12,345,678") == 12345678
    assert parse_int("") is None


def test_parse_signed_int_keeps_sign():
    assert parse_signed_int("-120000") == -120000
    assert parse_signed_int("+95000") == 95000
