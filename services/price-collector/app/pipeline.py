"""Batch orchestration for the C-4 Price Collector.

For each ticker the pipeline collects daily candles (OPT10081), investor flow
(OPT10059), and the basic snapshot (OPT10001), merges them into ``ohlcv_data``
rows, and upserts them. Each batch is bracketed by a ``collector_runs`` record.
"""

from dataclasses import dataclass, field

from app.collectors.daily_chart import DailyChartCollector
from app.collectors.investor_flow import InvestorFlowCollector
from app.collectors.stock_basic import StockBasicCollector
from app.kiwoom.client import KiwoomClient
from app.schemas.price import build_ohlcv_rows
from app.storage.repository import OhlcvRepository


@dataclass(frozen=True)
class TickerResult:
    ticker: str
    status: str  # "ok" | "skipped" | "failed"
    candle_count: int = 0
    written_count: int = 0
    error: str | None = None


@dataclass
class BatchResult:
    status: str = "success"
    collected_count: int = 0
    inserted_count: int = 0
    failed_count: int = 0
    results: list[TickerResult] = field(default_factory=list)


class PriceCollectionPipeline:
    def __init__(
        self,
        client: KiwoomClient,
        repository: OhlcvRepository,
        use_adjusted_price: bool = True
    ) -> None:
        self._repository = repository
        self._daily = DailyChartCollector(client, use_adjusted_price)
        self._flow = InvestorFlowCollector(client)
        self._basic = StockBasicCollector(client)

    def run(
        self,
        tickers: list[str],
        base_date: str | None = None,
        run_mode: str = "batch"
    ) -> BatchResult:
        run_id = self._repository.start_run(run_mode)
        batch = BatchResult()
        for ticker in tickers:
            result = self._run_ticker(ticker, base_date)
            batch.results.append(result)
            batch.collected_count += result.candle_count
            batch.inserted_count += result.written_count
            if result.status == "failed":
                batch.failed_count += 1

        if batch.failed_count == len(tickers) and tickers:
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

    def _run_ticker(self, ticker: str, base_date: str | None) -> TickerResult:
        try:
            stock_id = self._repository.resolve_stock_id(ticker)
            if stock_id is None:
                return TickerResult(
                    ticker=ticker,
                    status="skipped",
                    error=f"unknown ticker {ticker}"
                )

            candles = self._daily.collect(ticker, base_date)
            if not candles:
                return TickerResult(ticker=ticker, status="skipped")

            flows = self._flow.collect(ticker, base_date)
            basic = self._basic.collect(ticker, base_date)
            rows = build_ohlcv_rows(ticker, candles, flows, basic)
            written = self._repository.upsert_ohlcv(stock_id, rows)
            return TickerResult(
                ticker=ticker,
                status="ok",
                candle_count=len(candles),
                written_count=written
            )
        except Exception as exc:  # one bad ticker must not abort the batch
            return TickerResult(
                ticker=ticker,
                status="failed",
                error=f"{ticker}: {exc}"
            )
