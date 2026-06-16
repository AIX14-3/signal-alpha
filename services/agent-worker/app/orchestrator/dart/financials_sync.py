"""L1 정형 재무 적재 오케스트레이션 — 수집기(fnlttSinglAcntAll) → 리포지토리.

종목 × 최근 N개 사업연도 × 보고서코드를 돌며 정형 재무 fact 를 멱등 적재한다.
upsert 가 멱등이므로 재실행해도 행 수가 늘지 않고, 정정(rcept_no 갱신)만 반영된다.
(DartCorpCodeSyncService 와 동일한 sync 서비스 패턴)
"""

from __future__ import annotations

from datetime import date
from typing import Any, Protocol

_DEFAULT_REPRT_CODES = ("11011", "11012", "11013", "11014")


class FinancialsCollector(Protocol):
    async def collect(
        self, *, stock_code: str, bsns_year: int, reprt_code: str
    ) -> list[dict[str, Any]]:
        pass


class FinancialFactsRepository(Protocol):
    async def upsert_facts(self, entries: list[dict[str, Any]]) -> int:
        pass


class DartFinancialsSyncService:
    def __init__(
        self,
        *,
        collector: FinancialsCollector,
        repository: FinancialFactsRepository,
        lookback_years: int = 3,
        reprt_codes: tuple[str, ...] = _DEFAULT_REPRT_CODES,
        current_year: int | None = None,
    ) -> None:
        self._collector = collector
        self._repository = repository
        self._lookback_years = max(1, lookback_years)
        self._reprt_codes = tuple(reprt_codes) or _DEFAULT_REPRT_CODES
        self._current_year = current_year or date.today().year

    def _target_years(self) -> list[int]:
        # 당해연도부터 N년 소급. 당해연도 사업보고서(11011)는 아직 미공시라 013(빈값)으로
        # 안전 처리되지만, 당해연도 분기/반기 보고서는 이렇게 해야 수집된다(누락 방지).
        return [self._current_year - offset for offset in range(self._lookback_years)]

    async def sync_ticker(self, *, stock_code: str, stock_id: int | None = None) -> dict[str, Any]:
        years = self._target_years()
        upserted = 0
        fetched = 0
        empty: list[str] = []
        for bsns_year in years:
            for reprt_code in self._reprt_codes:
                facts = await self._collector.collect(
                    stock_code=stock_code,
                    bsns_year=bsns_year,
                    reprt_code=reprt_code,
                )
                if not facts:
                    empty.append(f"{bsns_year}/{reprt_code}")
                    continue
                if stock_id is not None:
                    for fact in facts:
                        fact.setdefault("stock_id", stock_id)
                fetched += len(facts)
                upserted += await self._repository.upsert_facts(facts)
        return {
            "ticker": stock_code,
            "years": years,
            "reprt_codes": list(self._reprt_codes),
            "fetched_count": fetched,
            "upserted_count": upserted,
            # 수집됐지만 필수키 누락으로 리포지토리에서 폐기된 행 수(조용한 유실 가시화).
            "skipped_count": fetched - upserted,
            "empty_periods": empty,
        }
