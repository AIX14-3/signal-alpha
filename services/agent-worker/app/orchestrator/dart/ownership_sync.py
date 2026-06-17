"""L2 지분·내부자 적재 오케스트레이션 — 수집기(majorstock/elestock) → 리포지토리.

종목별로 대량보유·임원/주요주주 소유 이벤트를 멱등 적재한다. upsert 가 멱등이므로 재실행해도
행 수가 늘지 않고, 정정(rcept_no 갱신)만 반영된다. L1(DartFinancialsSyncService)과 동일한 sync
서비스 패턴이지만, ownership API 는 corp_code 단위로 전체 보고 이력을 반환하므로
bsns_year/reprt_code 루프가 없어 더 단순하다.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Protocol


class OwnershipCollector(Protocol):
    async def collect(self, *, stock_code: str) -> list[dict[str, Any]]:
        pass


class OwnershipRepository(Protocol):
    async def upsert_events(self, entries: list[dict[str, Any]]) -> int:
        pass


class DartOwnershipSyncService:
    def __init__(
        self,
        *,
        collector: OwnershipCollector,
        repository: OwnershipRepository,
    ) -> None:
        self._collector = collector
        self._repository = repository

    async def sync_ticker(self, *, stock_code: str, stock_id: int | None = None) -> dict[str, Any]:
        events = await self._collector.collect(stock_code=stock_code)
        if stock_id is not None:
            for event in events:
                event.setdefault("stock_id", stock_id)
        fetched = len(events)
        upserted = await self._repository.upsert_events(events) if events else 0
        by_holder_type = dict(Counter(event.get("holder_type") for event in events))
        return {
            "ticker": stock_code,
            "fetched_count": fetched,
            "upserted_count": upserted,
            # 수집됐지만 필수키 누락으로 리포지토리에서 폐기된 행 수(조용한 유실 가시화).
            "skipped_count": fetched - upserted,
            "by_holder_type": by_holder_type,
        }
