"""L2 지분·내부자 적재 오케스트레이션 — 수집기(majorstock/elestock) → 리포지토리.

종목별로 대량보유·임원/주요주주 소유 이벤트를 멱등 적재한다. upsert 가 멱등이므로 재실행해도
행 수가 늘지 않고, 정정(rcept_no 갱신)만 반영된다. L1(DartFinancialsSyncService)과 동일한 sync
서비스 패턴이지만, ownership API 는 corp_code 단위로 전체 보고 이력을 반환하므로
bsns_year/reprt_code 루프가 없어 더 단순하다.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Protocol


# 회당 세부변동내역 enrich 상한(문서 fetch 폭발 방지). elestock 는 전체 이력을 반환하므로
# 미enrich 행을 한 번에 다 받으면 종목당 수천 fetch가 된다 → 최신 N건씩 여러 회차로 분산 드레인.
# 정상 운영에선 일일 신규만 남아 상한에 거의 안 걸린다. env DART_OWNERSHIP_DETAIL_LIMIT 로 조정.
_DEFAULT_DETAIL_LIMIT = 30


class OwnershipCollector(Protocol):
    async def collect(self, *, stock_code: str) -> list[dict[str, Any]]:
        pass

    async def fetch_trade_detail(self, *, rcept_no: str) -> tuple[str, float | None]:
        pass


class OwnershipRepository(Protocol):
    async def upsert_events(self, entries: list[dict[str, Any]]) -> int:
        pass

    async def list_unenriched_ownership(self, *, stock_id: int, limit: int) -> Any:
        pass

    async def update_trade_detail(
        self, *, corp_code: str, rcept_no: str, trade_type: str, unit_price: float | None
    ) -> None:
        pass


class DartOwnershipSyncService:
    def __init__(
        self,
        *,
        collector: OwnershipCollector,
        repository: OwnershipRepository,
        detail_limit: int = _DEFAULT_DETAIL_LIMIT,
    ) -> None:
        self._collector = collector
        self._repository = repository
        self._detail_limit = detail_limit

    async def sync_ticker(self, *, stock_code: str, stock_id: int | None = None) -> dict[str, Any]:
        events = await self._collector.collect(stock_code=stock_code)
        if stock_id is not None:
            for event in events:
                event.setdefault("stock_id", stock_id)
        fetched = len(events)
        upserted = await self._repository.upsert_events(events) if events else 0
        by_holder_type = dict(Counter(event.get("holder_type") for event in events))
        # 세부변동내역 enrich(거래유형·단가) — stock_id 알 때만, 미enrich 행 상한 내에서 bounded.
        detail = (
            await self.enrich_trade_detail(stock_id=stock_id)
            if stock_id is not None and self._detail_limit > 0
            else {"enriched": 0, "failed": 0, "candidates": 0}
        )
        return {
            "ticker": stock_code,
            "fetched_count": fetched,
            "upserted_count": upserted,
            # 수집됐지만 필수키 누락으로 리포지토리에서 폐기된 행 수(조용한 유실 가시화).
            "skipped_count": fetched - upserted,
            "by_holder_type": by_holder_type,
            "detail_enriched": detail,
        }

    async def enrich_trade_detail(self, *, stock_id: int) -> dict[str, int]:
        """미enrich(elestock) 보고서를 최신순 상한 내에서 본문 파싱해 trade_type·단가 적재.

        rcept 단위(문서 1건)로 처리하며, 네트워크 실패는 이번 회차만 건너뛰고(다음 회차 재시도)
        파싱 실패는 fetch_trade_detail 이 'other' 로 표기해 재-fetch 루프를 막는다."""
        candidates = list(await self._repository.list_unenriched_ownership(
            stock_id=stock_id, limit=self._detail_limit
        ))
        enriched = 0
        failed = 0
        for row in candidates:
            item = dict(row)
            try:
                trade_type, unit_price = await self._collector.fetch_trade_detail(
                    rcept_no=str(item["rcept_no"])
                )
            except Exception:  # noqa: BLE001 — 문서 fetch 실패는 이번 회차만 skip(다음 회차 재시도)
                failed += 1
                continue
            await self._repository.update_trade_detail(
                corp_code=str(item["corp_code"]),
                rcept_no=str(item["rcept_no"]),
                trade_type=trade_type,
                unit_price=unit_price,
            )
            enriched += 1
        return {"enriched": enriched, "failed": failed, "candidates": len(candidates)}
