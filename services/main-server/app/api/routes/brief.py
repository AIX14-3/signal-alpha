from __future__ import annotations

from collections import OrderedDict
from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.routes.auth import NOTICE
from app.api.routes.signals import _signal_list_item, _STATUS_PRIORITY
from app.core.database import get_database_pool

# 데일리 브리핑(S3) — "오늘 발행된 신호"를 종목별로 묶어 카드 목록으로 주는 전체 공개
# 대시보드. 리포트 전체 공개(#788)와 같은 결로 인증 없이 api.signals_current 를 재사용한다.
# signals.py 의 종목별 그룹핑/투영(_signal_list_item)을 그대로 재사용해 로직을 중복하지 않는다.
router = APIRouter(prefix="/api/brief", tags=["brief"])


@router.get("")
async def get_brief(
    limit: int = Query(default=30, ge=1, le=100),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """현재 발행된 신호를 종목당 1개 카드로 응집해 반환(전체 공개).

    정렬은 "사용자가 먼저 봐야 할 것" 기준 — ①주의 레벨 높은 순 → ②방향 세기(|점수-50|)
    큰 순. 상위 ``limit`` 개만 노출한다.
    """
    from signal_alpha_data_access.backend import SignalRepository

    # limit 은 **종목** 수 — 리포지토리가 (종목, run_key) 최신 1행으로 접어 주므로 여기서
    # 그대로 넘긴다. 예전엔 인자 없이 불러 기본값 50이 **행**에 걸렸고, 한 종목의 과거
    # 이력이 창을 다 먹어 브리핑에 몇 종목만 뜨는 원인이었다.
    async with pool.acquire() as connection:
        rows = await SignalRepository(connection).list_current_published(limit)

    grouped: "OrderedDict[int, list[dict[str, Any]]]" = OrderedDict()
    for row in rows:
        record = dict(row)
        grouped.setdefault(record["stock_id"], []).append(record)

    items = [_signal_list_item(stock_id, source_rows) for stock_id, source_rows in grouped.items()]
    items.sort(
        key=lambda it: (
            _STATUS_PRIORITY.get(it.get("warning_level") or "NORMAL", 0),
            abs((it.get("score") if it.get("score") is not None else 50) - 50),
        ),
        reverse=True,
    )
    return {"items": items[:limit], "count": len(items), "notice": NOTICE}
