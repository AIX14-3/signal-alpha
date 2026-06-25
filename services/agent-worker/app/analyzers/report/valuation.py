from __future__ import annotations

import json
from collections import Counter
from statistics import median
from typing import Any, Iterable, Mapping


def build_valuation_summary(rows: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    facts = [dict(row) for row in rows]
    implied_multiples = [_number(row.get("implied_multiple")) for row in facts]
    implied_multiples = [value for value in implied_multiples if value is not None]
    peer_gaps = [_peer_gap(row) for row in facts]
    peer_gaps = [value for value in peer_gaps if value is not None]

    valuation_count = len(facts)
    needs_review_count = sum(1 for row in facts if bool(row.get("needs_review")))
    needs_review_ratio = round(needs_review_count / valuation_count, 4) if valuation_count else 1.0
    data_status = "partial" if valuation_count == 0 or needs_review_ratio >= 0.5 else "ok"
    implied_multiple_variance = _variance(implied_multiples)
    risk_flags = []
    if data_status == "partial":
        risk_flags.append("valuation_review_required")
    scenario_band = _scenario_band(
        implied_multiples,
        variance=implied_multiple_variance,
        needs_review=data_status == "partial",
    )

    return {
        "valuation_count": valuation_count,
        "usable_multiple_count": len(implied_multiples),
        "implied_multiple_avg": _rounded(_avg(implied_multiples)),
        "implied_multiple_median": _rounded(median(implied_multiples)) if implied_multiples else None,
        "implied_multiple_variance": _rounded(implied_multiple_variance),
        "peer_gap_avg": _rounded(_avg(peer_gaps)),
        "needs_review_ratio": needs_review_ratio,
        "needs_review": data_status == "partial",
        "data_status": data_status,
        "risk_flags": risk_flags,
        "scenario_band": scenario_band,
        "methodology_mix": _methodology_items(row.get("methodology") for row in facts),
        "peer_group": _peer_items(_peer_names(facts)),
        "latest_facts": _latest_facts(facts),
    }


def _peer_gap(row: Mapping[str, Any]) -> float | None:
    implied = _number(row.get("implied_multiple"))
    applied = _number(row.get("applied_multiple"))
    if implied is None or applied is None:
        return None
    return implied - applied


def _avg(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def _variance(values: list[float]) -> float | None:
    if not values:
        return None
    average = sum(values) / len(values)
    return sum((value - average) ** 2 for value in values) / len(values)


def _scenario_band(
    implied_multiples: list[float],
    *,
    variance: float | None,
    needs_review: bool,
) -> dict[str, Any]:
    if not implied_multiples:
        return {
            "low_multiple": None,
            "base_multiple": None,
            "high_multiple": None,
            "dispersion_level": "unknown",
            "confidence_note": "valuation_data_required",
            "needs_review": True,
        }

    base = float(median(implied_multiples))
    spread = (variance or 0.0) ** 0.5
    dispersion_level = _dispersion_level(variance)
    return {
        "low_multiple": _rounded(max(0.0, base - spread)),
        "base_multiple": _rounded(base),
        "high_multiple": _rounded(base + spread),
        "dispersion_level": dispersion_level,
        "confidence_note": f"multiple_dispersion_{dispersion_level}",
        "needs_review": needs_review,
    }


def _dispersion_level(variance: float | None) -> str:
    if variance is None:
        return "unknown"
    if variance < 4:
        return "low"
    if variance < 16:
        return "medium"
    return "high"


def _rounded(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value, 4)


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _methodology_items(values: Iterable[Any]) -> list[dict[str, Any]]:
    counter = Counter(str(value).strip() for value in values if str(value or "").strip())
    return [
        {"methodology": name, "count": count}
        for name, count in sorted(counter.items(), key=lambda item: item[0])
    ]


def _peer_items(values: Iterable[Any]) -> list[dict[str, Any]]:
    counter = Counter(str(value).strip() for value in values if str(value or "").strip())
    return [
        {"name": name, "count": count}
        for name, count in sorted(counter.items(), key=lambda item: (-item[1], item[0]))
    ]


def _peer_names(facts: list[dict[str, Any]]) -> Iterable[str]:
    for row in facts:
        peer_group = row.get("peer_group") or []
        if isinstance(peer_group, str):
            try:
                peer_group = json.loads(peer_group)
            except json.JSONDecodeError:
                peer_group = []
        if not isinstance(peer_group, list):
            continue
        for peer in peer_group:
            if str(peer or "").strip():
                yield str(peer).strip()


def _latest_facts(facts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ordered = sorted(
        facts,
        key=lambda row: str(row.get("publish_date") or ""),
        reverse=True,
    )
    return [
        {
            "raw_document_id": row.get("raw_document_id"),
            "broker": row.get("broker"),
            "publish_date": str(row.get("publish_date")) if row.get("publish_date") is not None else None,
            "methodology": row.get("methodology") or "unknown",
            "implied_multiple": _number(row.get("implied_multiple")),
            "applied_multiple": _number(row.get("applied_multiple")),
            "category_tag": row.get("category_tag"),
            "needs_review": bool(row.get("needs_review")),
        }
        for row in ordered[:5]
    ]
