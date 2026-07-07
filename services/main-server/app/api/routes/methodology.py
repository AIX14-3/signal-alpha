from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends

from app.core.database import get_database_pool
from signal_alpha_data_access.backend import BacktestRepository, UserSignalRepository

router = APIRouter(prefix="/api/methodology", tags=["methodology"])

_MIN_SAMPLE_SIZE = 20
_HORIZON_LABELS = {"5td": "5거래일", "7td": "7거래일", "30td": "30거래일"}
_DIRECTION_LABELS = {"positive": "긍정 방향성", "negative": "부정 방향성"}


@router.get("/posthoc-alignment")
async def get_posthoc_alignment(pool: Any = Depends(get_database_pool)) -> dict[str, Any]:
    async with pool.acquire() as connection:
        journal_rows = await UserSignalRepository(connection).get_journal_posthoc_alignment_summary()
        signal_rows = await BacktestRepository(connection).get_signal_posthoc_alignment_summary()

    journal_items = [_alignment_item(dict(row)) for row in journal_rows]
    signal_items = [_alignment_item(dict(row)) for row in signal_rows]
    groups = [
        {
            "scope": "journal_based",
            "metric_label": "저널 기준 사후정합성",
            "items": journal_items,
        },
        {
            "scope": "signal_based",
            "metric_label": "전체 발행 신호 기준 사후정합성",
            "items": signal_items,
        },
    ]

    return {
        "scope": "journal_based",
        "metric_label": "저널 기준 사후정합성",
        "items": journal_items,
        "groups": groups,
        "methodology": {
            "basis": "저널에 저장된 발행 당시 데이터 방향성과 이후 확정된 관측 결과를 분리해 비교합니다.",
            "included": "positive/negative 방향성이 저장되고 7거래일 또는 30거래일 outcome이 확정된 케이스",
            "excluded": "neutral/mixed 방향, 확정 대기, 데이터 부족, 표본 부족 케이스",
            "signal_based_basis": "전체 발행 신호 기준은 별도 검증 기록에 남은 5거래일 확정 결과만 집계합니다.",
        },
        "notice": "사후정합성은 과거 데이터 방향성의 검증 기록이며 미래 결과를 보장하지 않습니다. 특정 종목 행동 권유가 아니라 사용자 판단 보조를 위한 근거입니다.",
    }


def _alignment_item(row: dict[str, Any]) -> dict[str, Any]:
    confirmed_count = int(row.get("confirmed_count") or 0)
    pending_count = int(row.get("pending_count") or 0)
    return {
        "horizon": row["horizon"],
        "label": _HORIZON_LABELS.get(row["horizon"], row["horizon"]),
        "alignment_rate": _number(row.get("alignment_rate")),
        "confirmed_count": confirmed_count,
        "aligned_count": int(row.get("aligned_count") or 0),
        "not_aligned_count": int(row.get("not_aligned_count") or 0),
        "pending_count": pending_count,
        "sample_status": _sample_status(confirmed_count, pending_count),
        "direction_breakdown": _direction_breakdown_items(row.get("direction_breakdown")),
        "first_outcome_trade_date": _timestamp(row.get("first_outcome_trade_date")),
        "last_outcome_trade_date": _timestamp(row.get("last_outcome_trade_date")),
        "checked_at": _timestamp(row.get("checked_at")),
    }


def _direction_breakdown_items(value: Any) -> list[dict[str, Any]]:
    raw_items = _json_items(value)
    items: list[dict[str, Any]] = []
    for raw_item in raw_items:
        if not isinstance(raw_item, dict):
            continue
        direction = str(raw_item.get("direction") or "")
        label = _DIRECTION_LABELS.get(direction)
        if label is None:
            continue
        confirmed_count = int(raw_item.get("confirmed_count") or 0)
        pending_count = int(raw_item.get("pending_count") or 0)
        items.append(
            {
                "direction": direction,
                "label": label,
                "alignment_rate": _number(raw_item.get("alignment_rate")),
                "confirmed_count": confirmed_count,
                "aligned_count": int(raw_item.get("aligned_count") or 0),
                "not_aligned_count": int(raw_item.get("not_aligned_count") or 0),
                "pending_count": pending_count,
                "sample_status": _sample_status(confirmed_count, pending_count),
            }
        )
    return items


def _json_items(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return parsed if isinstance(parsed, list) else []
    return value if isinstance(value, list) else []


def _sample_status(confirmed_count: int, pending_count: int) -> str:
    if confirmed_count == 0 and pending_count > 0:
        return "확정 대기"
    if confirmed_count < _MIN_SAMPLE_SIZE:
        return "표본 부족"
    return "집계 가능"


def _number(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
