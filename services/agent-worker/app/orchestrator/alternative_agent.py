"""AlternativeAgent — the diagram's "오케스트레이터 (연결자)".

Runs each registered source's loader+analyzer, collects the per-source signals,
and hands them to the AlternativeAggregator. It does NOT merge — that is the
aggregator's job (role separation).

Performance loop ①: the registered sources run concurrently via
``asyncio.gather``. Each source acquires its *own* pooled connection, because a
single asyncpg connection cannot serve concurrent queries — so the loads truly
overlap instead of running one after another (the old sequential
``for pipeline in pipelines`` shape).
"""

from __future__ import annotations

import asyncio
from datetime import date
from typing import Any, Callable, Sequence

from app.aggregator import AlternativeAggregator
from app.analyzers.registry import SourceRegistration, build_registry
from app.schemas.alternative_signal import AlternativeSignal
from app.schemas.source_result import SourceResult


def _default_repository_factory(connection: Any) -> Any:
    from signal_alpha_data_access.repositories import RawDetailRepository

    return RawDetailRepository(connection)


class AlternativeAgent:
    def __init__(
        self,
        *,
        registrations: Sequence[SourceRegistration] | None = None,
        aggregator: AlternativeAggregator | None = None,
        repository_factory: Callable[[Any], Any] = _default_repository_factory,
    ) -> None:
        self._registrations = list(registrations) if registrations is not None else build_registry()
        self._aggregator = aggregator or AlternativeAggregator()
        self._repository_factory = repository_factory

    async def run(
        self,
        *,
        pool: Any,
        stock_id: int,
        stock_code: str,
        as_of: date | None = None,
    ) -> AlternativeSignal:
        resolved_as_of = as_of or date.today()
        results = await asyncio.gather(
            *[
                self._run_source(registration, pool, stock_id, stock_code, resolved_as_of)
                for registration in self._registrations
            ]
        )
        return self._aggregator.merge(list(results), stock_code=stock_code)

    async def _run_source(
        self,
        registration: SourceRegistration,
        pool: Any,
        stock_id: int,
        stock_code: str,
        as_of: date,
    ) -> SourceResult:
        try:
            async with pool.acquire() as connection:
                repository = self._repository_factory(connection)
                loader = registration.build_loader(repository)
                evidence = await loader.load(
                    stock_id=stock_id,
                    stock_code=stock_code,
                    as_of=as_of,
                )
            return await registration.analyzer.analyze(stock_code, evidence)
        except Exception as exc:  # one source failing must not sink the others
            return SourceResult(
                source=registration.source,
                stock_code=stock_code,
                direction="unknown",
                score=0.0,
                summary=f"{registration.source} 분석 실패: {exc}",
                risk_flags=["analyzer_error"],
                data_status="failed",
            )
