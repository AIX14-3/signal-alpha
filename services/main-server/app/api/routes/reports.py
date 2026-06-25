from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.auth import (
    NOTICE,
    _subscription_active,
    get_current_user,
    get_current_user_optional,
)
from app.core.config import Settings, get_settings
from app.core.database import get_database_pool
from signal_alpha_data_access.backend import (
    ReportIssuanceRepository,
    SignalRepository,
)

router = APIRouter(prefix="/api/reports", tags=["reports"])

# 연결점 → score_breakdown 키 / signal_events.source_type 매핑.
# REPORT 는 임베딩/RAG 분석 제거로 점수를 산출하지 않으므로 사용자 노출 소스에서 제외
# (집계 SOURCE_ORDER 와 일치). 리포트 원본은 수집/파싱/정규화로 DB 에는 계속 적재된다.
ALL_SOURCES = ("price", "dart", "hiring", "datalab")
PUBLIC_SOURCES = {"dart", "datalab"}  # 비회원 공개
_SOURCE_TO_BREAKDOWN = {
    "price": "PRICE",
    "dart": "DART",
    "hiring": "ALTERNATIVE",
    "datalab": "ALTERNATIVE",
}
_SOURCE_TO_EVENT_TYPE = {
    "price": "PRICE",
    "dart": "DART",
    "hiring": "HIRING",
    "datalab": "DATALAB",
}
_BLIND_NOTICE = "전체 리포트는 로그인 후 무료 3회까지 열람할 수 있습니다. 비회원은 DART·네이버 데이터만 확인할 수 있습니다."


@router.get("/quota")
async def get_quota(
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        free_used = await ReportIssuanceRepository(connection).count_free_used(
            user_id=int(current_user["id"])
        )
        subscription_active = await _subscription_active(connection, int(current_user["id"]))
    quota = settings.free_report_quota
    return {
        "free_quota": quota,
        "free_used": free_used,
        "free_remaining": max(0, quota - free_used),
        "subscription_active": subscription_active,
        "notice": NOTICE,
    }


@router.get("/{stock_code}")
async def get_report(
    stock_code: str,
    current_user: dict[str, Any] | None = Depends(get_current_user_optional),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        row = await SignalRepository(connection).get_current_by_ticker(stock_code)
        if row is None:
            raise _api_error(404, "REPORT_NOT_FOUND", "발행된 리포트가 없습니다.")
        row = dict(row)
        is_member = current_user is not None
        unlocked = False
        if is_member:
            unlocked = await _is_unlocked(connection, int(current_user["id"]), int(row["id"]))
    return _report_response(row, unlocked=unlocked, is_member=is_member)


@router.post("/{stock_code}/issue")
async def issue_report(
    stock_code: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
    settings: Settings = Depends(get_settings),
) -> dict[str, Any]:
    user_id = int(current_user["id"])
    async with pool.acquire() as connection:
        row = await SignalRepository(connection).get_current_by_ticker(stock_code)
        if row is None:
            raise _api_error(404, "REPORT_NOT_FOUND", "발행된 리포트가 없습니다.")
        row = dict(row)
        final_signal_id = int(row["id"])
        issuance_repo = ReportIssuanceRepository(connection)

        existing = await issuance_repo.get_issuance(user_id=user_id, final_signal_id=final_signal_id)
        subscription_active = await _subscription_active(connection, user_id)
        free_used = await issuance_repo.count_free_used(user_id=user_id)
        quota = settings.free_report_quota

        if existing is not None:
            issued_via = existing["issued_via"]
        elif subscription_active:
            await issuance_repo.insert_issuance(
                user_id=user_id,
                stock_id=int(row["stock_id"]),
                final_signal_id=final_signal_id,
                run_key=str(row.get("run_key") or "AGGREGATED"),
                issued_via="subscription",
            )
            issued_via = "subscription"
        elif quota - free_used > 0:
            await issuance_repo.insert_issuance(
                user_id=user_id,
                stock_id=int(row["stock_id"]),
                final_signal_id=final_signal_id,
                run_key=str(row.get("run_key") or "AGGREGATED"),
                issued_via="free",
            )
            issued_via = "free"
            free_used += 1
        else:
            raise _api_error(
                402,
                "REPORT_QUOTA_EXCEEDED",
                "무료 리포트 열람 횟수를 모두 사용했습니다. 구독 시 무제한으로 열람할 수 있습니다.",
            )

    return _report_response(
        row,
        unlocked=True,
        is_member=True,
        issued_via=issued_via,
        free_remaining=max(0, quota - free_used),
    )


@router.get("/{stock_code}/sources/{source}")
async def get_source_detail(
    stock_code: str,
    source: str,
    current_user: dict[str, Any] | None = Depends(get_current_user_optional),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    source = source.lower()
    if source not in ALL_SOURCES:
        raise _api_error(404, "SOURCE_NOT_FOUND", "알 수 없는 소스입니다.")

    async with pool.acquire() as connection:
        row = await SignalRepository(connection).get_current_by_ticker(stock_code)
        if row is None:
            raise _api_error(404, "REPORT_NOT_FOUND", "발행된 리포트가 없습니다.")
        row = dict(row)
        is_member = current_user is not None
        unlocked = is_member and await _is_unlocked(
            connection, int(current_user["id"]), int(row["id"])
        )

        # 접근 통제: 공개 소스(dart/datalab)는 누구나. 그 외는 회원+언락 필요.
        if source not in PUBLIC_SOURCES:
            if not is_member:
                raise _api_error(401, "MEMBERSHIP_REQUIRED", "로그인 후 열람할 수 있습니다.")
            if not unlocked:
                raise _api_error(
                    402,
                    "REPORT_QUOTA_EXCEEDED",
                    "리포트를 먼저 발행(열람)해야 소스 상세를 볼 수 있습니다.",
                )

        detail = await SignalRepository(connection).get_detail_by_id(int(row["id"]))

    breakdown = _json_object(row.get("score_breakdown"))
    block = _source_block(source, breakdown, locked=False)
    events = _json_array(dict(detail).get("signal_events")) if detail is not None else []
    event_type = _SOURCE_TO_EVENT_TYPE[source]
    items = [_evidence(e) for e in events if str(e.get("source_type", "")).upper() == event_type]

    return {
        "stock": _stock(row),
        "source": source,
        "direction": block.get("direction"),
        "score": block.get("score"),
        "data_status": block.get("data_status"),
        "summary": block.get("summary"),
        "items": items,
        "notice": NOTICE,
    }


# ----- helpers -----


async def _is_unlocked(connection: Any, user_id: int, final_signal_id: int) -> bool:
    if await _subscription_active(connection, user_id):
        return True
    issuance = await ReportIssuanceRepository(connection).get_issuance(
        user_id=user_id, final_signal_id=final_signal_id
    )
    return issuance is not None


def _report_response(
    row: dict[str, Any],
    *,
    unlocked: bool,
    is_member: bool,
    issued_via: str | None = None,
    free_remaining: int | None = None,
) -> dict[str, Any]:
    breakdown = _json_object(row.get("score_breakdown"))
    sources = [
        _source_block(s, breakdown, locked=(not unlocked and s not in PUBLIC_SOURCES))
        for s in ALL_SOURCES
    ]
    access: dict[str, Any] = {"unlocked": unlocked, "is_member": is_member}
    if issued_via is not None:
        access["issued_via"] = issued_via
    if free_remaining is not None:
        access["free_remaining"] = free_remaining

    return {
        "stock": _stock(row),
        "report_version": {
            "final_signal_id": row["id"],
            "run_key": row.get("run_key"),
            "signal_date": _iso(row.get("signal_date")),
            "updated_at": _iso(row.get("published_at") or row.get("created_at")),
        },
        "direction": row.get("signal") if unlocked else None,
        "score": _number(row.get("final_score")) if unlocked else None,
        "alignment_rate": _alignment(row.get("confidence")) if unlocked else None,
        "source_agreement": row.get("source_agreement") if unlocked else None,
        "warning_level": row.get("warning_level") if unlocked else None,
        "data_status": "ok" if unlocked else "partial",
        "summary": row.get("summary") if unlocked else None,
        "sources": sources,
        "access": access,
        "notice": NOTICE if unlocked else _BLIND_NOTICE,
    }


def _source_block(source: str, breakdown: dict[str, Any], *, locked: bool) -> dict[str, Any]:
    if locked:
        return {"source": source, "locked": True}
    detail = breakdown.get(_SOURCE_TO_BREAKDOWN[source])
    if not isinstance(detail, dict):
        detail = {}
    # ALTERNATIVE 하위에 hiring/datalab 가 중첩돼 있으면 사용.
    if source in ("hiring", "datalab"):
        nested = detail.get(source)
        if isinstance(nested, dict):
            detail = nested
    return {
        "source": source,
        "direction": detail.get("direction", "unknown"),
        "score": _number(detail.get("score_100", detail.get("score"))),
        "data_status": detail.get("data_status", "missing"),
        "summary": detail.get("summary"),
        "locked": False,
    }


def _stock(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("stock_id"),
        "stock_code": row.get("ticker"),
        "stock_name": row.get("name"),
        "market": row.get("market"),
        "sector": row.get("sector"),
    }


def _evidence(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": event.get("title"),
        "summary": event.get("summary"),
        "event_date": _iso(event.get("event_date")),
        "direction": event.get("signal_direction"),
        "impact_level": event.get("impact_level"),
        "evidence_url": event.get("evidence_url"),
        "source_name": event.get("source_name"),
        "is_official": event.get("is_official"),
    }


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    return []


def _number(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return int(num) if num.is_integer() else num


def _alignment(value: Any) -> float | None:
    num = _number(value)
    if num is None:
        return None
    return round(num / 100, 4)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
