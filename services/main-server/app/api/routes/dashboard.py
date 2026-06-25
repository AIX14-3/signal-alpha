from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends

from app.api.routes.auth import NOTICE, get_current_user
from app.core.database import get_database_pool
from signal_alpha_data_access.backend import SignalRepository, UserSignalRepository


SOURCE_ORDER = ("DART", "PRICE", "REPORT", "ALTERNATIVE")

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


@router.get("")
async def get_dashboard(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        user_signal_repository = UserSignalRepository(connection)
        watchlist_rows = [
            dict(row)
            for row in await user_signal_repository.list_watchlist(user_id=int(current_user["id"]))
        ]
        stock_ids = [int(row["stock_id"]) for row in watchlist_rows]
        signal_rows = [
            dict(row) for row in await SignalRepository(connection).list_current_by_stock_ids(stock_ids)
        ]
        journal_rows = [
            dict(row)
            for row in await user_signal_repository.list_latest_journals_by_stock_ids(
                user_id=int(current_user["id"]),
                stock_ids=stock_ids,
            )
        ]

    signals_by_stock_id = {int(row["stock_id"]): row for row in signal_rows}
    journals_by_stock_id = {int(row["stock_id"]): row for row in journal_rows}
    items = [
        _dashboard_item(
            watchlist=watchlist,
            signal=signals_by_stock_id.get(int(watchlist["stock_id"])),
            journal=journals_by_stock_id.get(int(watchlist["stock_id"])),
        )
        for watchlist in watchlist_rows
    ]

    return {
        "user": {
            "id": current_user["id"],
            "email": current_user["email"],
            "nickname": current_user.get("nickname"),
        },
        "watchlist_count": len(items),
        "items": items,
        "notice": NOTICE,
    }


def _dashboard_item(
    *,
    watchlist: dict[str, Any],
    signal: dict[str, Any] | None,
    journal: dict[str, Any] | None,
) -> dict[str, Any]:
    return {
        "stock": _stock_response(watchlist),
        "latest_signal": _latest_signal_response(signal),
        "source_summary": _source_summary(signal),
        "journal": {
            "has_journal": journal is not None,
            "latest_journal_id": journal["id"] if journal else None,
        },
    }


def _stock_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["stock_id"],
        "stock_code": row["ticker"],
        "stock_name": row["name"],
        "market": row.get("market"),
        "sector": row.get("sector"),
    }


def _latest_signal_response(signal: dict[str, Any] | None) -> dict[str, Any]:
    if signal is None:
        return {
            "signal_id": None,
            "direction": "unknown",
            "score": None,
            "alignment_rate": None,
            "data_status": "missing",
            "needs_review": False,
            "summary": None,
            "source_count": 0,
            "updated_at": None,
        }
    score_breakdown = _score_breakdown(signal)
    return {
        "signal_id": signal["id"],
        "direction": signal["signal"],
        "score": _number(signal.get("final_score")),
        "alignment_rate": _alignment_rate(signal.get("consensus_score")),
        "data_status": "ok",
        "needs_review": bool(signal.get("needs_review", False)),
        "summary": signal.get("summary"),
        "source_count": _source_count(score_breakdown),
        "updated_at": _timestamp(signal.get("published_at") or signal.get("created_at")),
    }


def _source_summary(signal: dict[str, Any] | None) -> list[dict[str, Any]]:
    score_breakdown = _score_breakdown(signal)
    return [_source_item(source, score_breakdown.get(source)) for source in SOURCE_ORDER]


def _source_item(source: str, detail: Any) -> dict[str, Any]:
    if not isinstance(detail, dict):
        return {
            "source": source,
            "direction": "unknown",
            "score": None,
            "data_status": "missing",
        }
    return {
        "source": source,
        "direction": detail.get("direction", "unknown"),
        "score": _number(detail.get("score")),
        "data_status": detail.get("data_status", "ok"),
    }


def _score_breakdown(signal: dict[str, Any] | None) -> dict[str, Any]:
    if signal is None:
        return {}
    value = signal.get("score_breakdown") or {}
    if isinstance(value, dict):
        return value
    return {}


def _source_count(score_breakdown: dict[str, Any]) -> int:
    return sum(1 for source in SOURCE_ORDER if isinstance(score_breakdown.get(source), dict))


def _alignment_rate(consensus_score: Any) -> float | None:
    value = _number(consensus_score)
    if value is None:
        return None
    return round(value / 100, 4)


def _number(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)
