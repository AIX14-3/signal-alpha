from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.auth import NOTICE, get_current_user
from app.core.database import get_database_pool

SOURCE_ORDER = ("DART", "PRICE", "REPORT", "ALTERNATIVE")

router = APIRouter(tags=["signals"])


@router.get("/signals/{ticker}")
async def get_current_signal(ticker: str, pool: Any = Depends(get_database_pool)) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import SignalRepository

    async with pool.acquire() as connection:
        row = await SignalRepository(connection).get_current_by_ticker(ticker)

    if row is None:
        raise HTTPException(status_code=404, detail="Signal not found.")

    return dict(row)


@router.get("/api/signals/{signal_id}")
async def get_signal_detail(
    signal_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    from signal_alpha_data_access.repositories import SignalRepository

    async with pool.acquire() as connection:
        row = await SignalRepository(connection).get_detail_by_id(signal_id)

    if row is None:
        raise _api_error(404, "SIGNAL_NOT_FOUND", "시그널을 찾을 수 없습니다.")

    return _signal_detail_response(dict(row))


def _signal_detail_response(row: dict[str, Any]) -> dict[str, Any]:
    score_breakdown = _json_object(row.get("score_breakdown"))
    agent_results = _json_array(row.get("agent_results"))
    signal_events = _json_array(row.get("signal_events"))
    return {
        "signal_id": row["id"],
        "stock": {
            "id": row["stock_id"],
            "stock_code": row["ticker"],
            "stock_name": row["name"],
            "market": row.get("market"),
            "sector": row.get("sector"),
        },
        "direction": row["signal"],
        "score": _number(row.get("final_score")),
        "alignment_rate": _alignment_rate(row.get("consensus_score") or row.get("confidence")),
        "source_agreement": row.get("source_agreement"),
        "warning_level": row.get("warning_level"),
        "data_status": _overall_data_status(row),
        "needs_review": bool(row.get("needs_review", False)),
        "summary": row.get("summary"),
        "positive_evidence": _json_array(row.get("positive_evidence")),
        "caution_evidence": _json_array(row.get("caution_evidence")),
        "sources": [
            _source_response(
                source=source,
                detail=score_breakdown.get(source),
                agent_results=agent_results,
                signal_events=signal_events,
            )
            for source in SOURCE_ORDER
        ],
        "notice": NOTICE,
    }


def _source_response(
    *,
    source: str,
    detail: Any,
    agent_results: list[Any],
    signal_events: list[Any],
) -> dict[str, Any]:
    if not isinstance(detail, dict):
        detail = {}
    agent_result = _agent_result_for_source(
        agent_results=agent_results,
        source=source,
        agent_result_id=detail.get("agent_result_id"),
    )
    method_detail = _json_object(agent_result.get("method_detail")) if agent_result else {}
    return {
        "source": source,
        "direction": detail.get("direction", "unknown"),
        "score": _source_score(detail),
        "data_status": detail.get("data_status", "missing"),
        "needs_review": bool(detail.get("needs_review", detail.get("data_status") == "missing")),
        "summary": method_detail.get("summary") or detail.get("summary"),
        "risk_flags": _json_array(detail.get("risk_flags")),
        "evidence": _source_evidence(agent_result, source, signal_events),
    }


def _agent_result_for_source(
    *,
    agent_results: list[Any],
    source: str,
    agent_result_id: Any,
) -> dict[str, Any] | None:
    if agent_result_id is not None:
        for agent_result in agent_results:
            if isinstance(agent_result, dict) and agent_result.get("id") == agent_result_id:
                return agent_result
    for agent_result in agent_results:
        if not isinstance(agent_result, dict):
            continue
        method_detail = _json_object(agent_result.get("method_detail"))
        if _normalize_source(method_detail.get("source") or method_detail.get("source_type")) == source:
            return agent_result
    return None


def _source_evidence(
    agent_result: dict[str, Any] | None,
    source: str,
    signal_events: list[Any],
) -> list[dict[str, Any]]:
    event_ids = set(_int_list(agent_result.get("source_signal_event_ids") if agent_result else None))
    items: list[dict[str, Any]] = []
    for event in signal_events:
        if not isinstance(event, dict):
            continue
        if event_ids and int(event["id"]) not in event_ids:
            continue
        if not event_ids and _normalize_source(event.get("source_type")) != source:
            continue
        items.append(
            {
                "id": event.get("id"),
                "source_document_id": event.get("source_document_id"),
                "event_type": event.get("event_type"),
                "event_date": _timestamp(event.get("event_date")),
                "direction": event.get("signal_direction"),
                "impact_level": event.get("impact_level"),
                "title": event.get("title"),
                "summary": event.get("summary"),
                "evidence_url": event.get("evidence_url") or event.get("source_url"),
                "source_name": event.get("source_name"),
                "is_official": event.get("is_official"),
                "needs_review": bool(event.get("needs_review", False)),
            }
        )
    return items


def _overall_data_status(row: dict[str, Any]) -> str:
    if row.get("warning_level") == "WARNING":
        return "failed"
    if row.get("warning_level") == "CAUTION" or row.get("needs_review"):
        return "partial"
    return "ok"


def _source_score(detail: dict[str, Any]) -> float | int | None:
    score_100 = _number(detail.get("score_100"))
    if score_100 is not None:
        return score_100
    score = _number(detail.get("score"))
    if score is None:
        return None
    if -1 <= float(score) <= 1:
        return _number(round((float(score) + 1) * 50, 2))
    return score


def _alignment_rate(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return round(float(number) / 100, 4)


def _json_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return dict(value)


def _json_array(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    return list(value)


def _int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list):
        return [int(item) for item in value]
    if isinstance(value, tuple):
        return [int(item) for item in value]
    if isinstance(value, str):
        parsed = json.loads(value)
        return [int(item) for item in parsed]
    return [int(value)]


def _normalize_source(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in SOURCE_ORDER:
        return text
    if text in {"HIRING", "PATENT", "DATALAB"}:
        return "ALTERNATIVE"
    return None


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


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
