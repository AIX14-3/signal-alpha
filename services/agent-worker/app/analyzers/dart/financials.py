from __future__ import annotations

import re
from decimal import Decimal
from typing import Any


_METRIC_PATTERNS = [
    ("dart_revenue", re.compile(r"(?:매출액|매출|revenue)\D{0,20}([-+]?\d[\d,]*(?:\.\d+)?)\s*(조원|억원|백만원|million\s*krw|billion\s*krw)?", re.IGNORECASE)),
    (
        "dart_operating_profit",
        re.compile(
            r"(?:영업이익|operating\s+profit)\D{0,20}([-+]?\d[\d,]*(?:\.\d+)?)\s*(조원|억원|백만원|million\s*krw|billion\s*krw)?",
            re.IGNORECASE,
        ),
    ),
    (
        "dart_net_income",
        re.compile(
            r"(?:당기순이익|순이익|net\s+income)\D{0,20}([-+]?\d[\d,]*(?:\.\d+)?)\s*(조원|억원|백만원|million\s*krw|billion\s*krw)?",
            re.IGNORECASE,
        ),
    ),
]

_UNIT_TO_KRW_MILLION = {
    "백만원": Decimal("1"),
    "millionkrw": Decimal("1"),
    "억원": Decimal("100"),
    "billionkrw": Decimal("1000"),
    "조원": Decimal("1000000"),
}


def extract_dart_financial_metrics(text: str | None) -> list[dict[str, Any]]:
    if not text:
        return []

    metrics: list[dict[str, Any]] = []
    for metric_name, pattern in _METRIC_PATTERNS:
        match = pattern.search(text)
        if match is None:
            continue
        number_text, unit_text = match.groups()
        metrics.append(
            {
                "metric_name": metric_name,
                "metric_value": _to_krw_million(number_text, unit_text),
                "metric_unit": "KRW_million",
            }
        )
    return metrics


def _to_krw_million(number_text: str, unit_text: str | None) -> int | float:
    number = Decimal(number_text.replace(",", ""))
    unit_key = (unit_text or "백만원").lower().replace(" ", "")
    value = number * _UNIT_TO_KRW_MILLION.get(unit_key, Decimal("1"))
    if value == value.to_integral_value():
        return int(value)
    return float(value)
