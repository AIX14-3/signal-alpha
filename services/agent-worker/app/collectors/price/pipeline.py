"""Polling pipeline: read target stocks from the DB, poll Kiwoom REST,
persist snapshots. One failing stock never aborts the sweep.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime

from app.collectors.price.investor_flow import fetch_investor_flows, pick_flow_for_date
from app.collectors.price.stock_snapshot import fetch_stock_snapshot
from app.collectors.price.kiwoom.rest_client import KiwoomRestClient
from app.collectors.price.schemas import TargetStock
from app.collectors.price.repository import PriceSnapshotRepository

logger = logging.getLogger(__name__)


@dataclass
class CycleStats:
    collected: int = 0
    stored: int = 0
    skipped: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def merge(self, other: "CycleStats") -> None:
        self.collected += other.collected
        self.stored += other.stored
        self.skipped += other.skipped
        self.failed += other.failed
        self.errors.extend(other.errors)


async def run_snapshot_cycle(
    client: KiwoomRestClient,
    repository: PriceSnapshotRepository,
    targets: list[TargetStock],
    captured_at: datetime,
) -> CycleStats:
    stats = CycleStats()
    for target in targets:
        try:
            snapshot = await fetch_stock_snapshot(client, target.ticker, captured_at)
            stats.collected += 1
            await repository.insert_snapshot(target.stock_id, snapshot)
            stats.stored += 1
            if not await repository.upsert_intraday_ohlcv(target.stock_id, snapshot):
                stats.skipped += 1
                logger.info("%s: partial OHLC, ohlcv_data upsert skipped", target.ticker)
        except Exception as exc:  # noqa: BLE001 - one stock must not stop the sweep
            stats.failed += 1
            stats.errors.append(f"{target.ticker}: {exc}")
            logger.warning("snapshot failed for %s: %s", target.ticker, exc)
    return stats


async def run_investor_flow_update(
    client: KiwoomRestClient,
    repository: PriceSnapshotRepository,
    targets: list[TargetStock],
    trade_date: date,
) -> CycleStats:
    stats = CycleStats()
    for target in targets:
        try:
            flows = await fetch_investor_flows(client, target.ticker, trade_date)
            stats.collected += 1
            flow = pick_flow_for_date(flows, trade_date)
            if flow is None:
                stats.skipped += 1
                logger.info("%s: no confirmed flow row for %s yet", target.ticker, trade_date)
                continue
            if await repository.update_investor_flow(target.stock_id, flow):
                stats.stored += 1
            else:
                stats.skipped += 1
                logger.info(
                    "%s: no ohlcv_data row for %s, flow update skipped",
                    target.ticker,
                    trade_date,
                )
        except Exception as exc:  # noqa: BLE001
            stats.failed += 1
            stats.errors.append(f"{target.ticker}: {exc}")
            logger.warning("flow update failed for %s: %s", target.ticker, exc)
    return stats
