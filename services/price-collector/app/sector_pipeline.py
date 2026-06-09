"""Batch orchestration for sector index collection (#27).

For each active sector the pipeline collects the daily index chart (OPT20006),
merges it into ``sector_ohlcv`` rows, and upserts them. The KOSPI/KOSDAQ market
indices are just sectors with ``is_market_index = TRUE``, so they flow through
the same loop and become the denominator for downstream relative-strength.
"""

from dataclasses import dataclass, field

from app.collectors.sector_daily_chart import SectorDailyChartCollector
from app.kiwoom.client import KiwoomClient
from app.schemas.sector import SectorRef, build_sector_ohlcv_rows
from app.storage.sector_repository import SectorRepository


@dataclass(frozen=True)
class SectorResult:
    kiwoom_code: str
    market: str
    status: str  # "ok" | "skipped" | "failed"
    candle_count: int = 0
    written_count: int = 0
    error: str | None = None


@dataclass
class SectorBatchResult:
    status: str = "success"
    collected_count: int = 0
    inserted_count: int = 0
    failed_count: int = 0
    results: list[SectorResult] = field(default_factory=list)


class SectorCollectionPipeline:
    def __init__(
        self,
        client: KiwoomClient,
        repository: SectorRepository
    ) -> None:
        self._repository = repository
        self._daily = SectorDailyChartCollector(client)

    def run(
        self,
        base_date: str | None = None,
        run_mode: str = "batch"
    ) -> SectorBatchResult:
        sectors = self._repository.list_active_sectors()
        run_id = self._repository.start_run(run_mode)
        batch = SectorBatchResult()
        for sector in sectors:
            result = self._run_sector(sector, base_date)
            batch.results.append(result)
            batch.collected_count += result.candle_count
            batch.inserted_count += result.written_count
            if result.status == "failed":
                batch.failed_count += 1

        if batch.failed_count == len(sectors) and sectors:
            batch.status = "failed"
        elif batch.failed_count:
            batch.status = "partial"

        errors = [r.error for r in batch.results if r.error]
        self._repository.finish_run(
            run_id=run_id,
            status=batch.status,
            collected_count=batch.collected_count,
            inserted_count=batch.inserted_count,
            failed_count=batch.failed_count,
            error_message="; ".join(errors) if errors else None
        )
        return batch

    def _run_sector(
        self,
        sector: SectorRef,
        base_date: str | None
    ) -> SectorResult:
        try:
            candles = self._daily.collect(sector.kiwoom_code, base_date)
            if not candles:
                return SectorResult(
                    kiwoom_code=sector.kiwoom_code,
                    market=sector.market,
                    status="skipped"
                )
            rows = build_sector_ohlcv_rows(sector.id, candles)
            written = self._repository.upsert_sector_ohlcv(rows)
            return SectorResult(
                kiwoom_code=sector.kiwoom_code,
                market=sector.market,
                status="ok",
                candle_count=len(candles),
                written_count=written
            )
        except Exception as exc:  # one bad sector must not abort the batch
            return SectorResult(
                kiwoom_code=sector.kiwoom_code,
                market=sector.market,
                status="failed",
                error=f"{sector.market}/{sector.kiwoom_code}: {exc}"
            )
