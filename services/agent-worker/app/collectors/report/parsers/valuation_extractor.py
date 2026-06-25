from __future__ import annotations

import re
from typing import Any

METHODOLOGIES = ("EV_EBITDA", "PER", "PBR", "SOTP", "DCF")


def extract_valuation_facts(
    text: str,
    *,
    target_price: int | None,
    broker: str | None = None,
    publish_date: Any | None = None,
) -> dict[str, Any]:
    forward_eps_est, eps_fy = _extract_forward_eps(text)
    applied_multiple = _extract_applied_multiple(text)
    methodology = _extract_methodology(text, applied_multiple)
    implied_multiple = _calculate_implied_multiple(target_price, forward_eps_est)
    peer_group = _extract_peer_group(text)
    needs_review = target_price is None or forward_eps_est is None or implied_multiple is None

    return {
        "broker": broker,
        "publish_date": str(publish_date) if publish_date is not None else None,
        "target_price": target_price,
        "forward_eps_est": forward_eps_est,
        "eps_fy": eps_fy,
        "methodology": methodology,
        "applied_multiple": applied_multiple,
        "implied_multiple": implied_multiple,
        "peer_group": peer_group,
        "category_tag": None,
        "rerating_thesis": None,
        "extraction_source": "rules",
        "needs_review": needs_review,
    }


def _extract_forward_eps(text: str) -> tuple[int | None, int | None]:
    patterns = [
        r"(20\d{2})\s*[EF]?\s*(?:지배주주\s*)?(?:EPS|주당순이익)\D{0,20}([0-9][0-9,.]*)\s*(?:원|KRW)?",
        r"(20\d{2})\s*[EF]?\s*EPS\D{0,20}([0-9][0-9,.]*)\s*(?:원|KRW)?",
        r"(?:EPS|주당순이익)\s*(?:\(?\s*(20\d{2})\s*[EF]?\s*\)?)?\D{0,20}([0-9][0-9,.]*)\s*(?:원|KRW)?",
        r"EPS\s*(?:\(?\s*(20\d{2})\s*[EF]?\s*\)?)?\D{0,20}([0-9][0-9,.]*)\s*(?:원|KRW)?",
        r"([0-9][0-9,.]*)\s*(?:원|KRW)?\D{0,20}(?:20\d{2}\s*[EF]?\s*)?EPS",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if not match:
            continue
        if len(match.groups()) >= 2 and match.group(1) and match.group(1).isdigit() and len(match.group(1)) == 4:
            return _parse_int(match.group(2)), int(match.group(1))
        return _parse_int(match.group(2) if len(match.groups()) >= 2 else match.group(1)), None
    return None, None


def _extract_applied_multiple(text: str) -> float | None:
    pattern = r"(?:target|applied|목표|적용)?\s*(?:PER|PBR|EV/?EBITDA)\D{0,20}([0-9]+(?:\.[0-9]+)?)\s*(?:x|배)"
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None


def _extract_methodology(text: str, applied_multiple: float | None) -> str:
    normalized = text.upper().replace("/", "_").replace("-", "_")
    if "EV_EBITDA" in normalized or "EVEBITDA" in normalized:
        return "EV_EBITDA"
    for methodology in METHODOLOGIES[1:]:
        if re.search(rf"\b{methodology}\b", normalized):
            return methodology
    if applied_multiple is not None:
        return "PER"
    return "unknown"


def _calculate_implied_multiple(target_price: int | None, forward_eps_est: int | None) -> float | None:
    if target_price is None or forward_eps_est is None or forward_eps_est <= 0:
        return None
    return round(target_price / forward_eps_est, 4)


def _extract_peer_group(text: str) -> list[str]:
    match = re.search(r"peer\s*group\s*:\s*([^\n\r]+)", text, flags=re.IGNORECASE)
    if not match:
        return []
    return [
        item.strip()
        for item in re.split(r"[,;/]", match.group(1))
        if item.strip()
    ][:10]


def _parse_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(float(value.replace(",", "")))
    except ValueError:
        return None
