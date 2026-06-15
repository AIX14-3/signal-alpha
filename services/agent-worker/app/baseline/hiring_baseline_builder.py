"""DataLabBaselineCollector — fills hiring_baseline from '{company} 채용' search trends.

Hiring data is sparse early on, so "today's N postings" needs a seasonal baseline
to be meaningful. Migration 014 designed ``hiring_baseline`` (per-stock average +
quarterly seasonal factors) for exactly this; HiringCollector already reads it.

This builder is NOT a raw collector: it reads the Naver DataLab API and writes
ONLY to ``hiring_baseline``. It never touches raw_documents / *_raw_details /
processing_queue.

Flow per stock (see plan):
  1. HiringKeywordGenerator → '{company} 채용' keyword group
  2. NaverDataLabClient.search_grouped(time_unit='month') over ~3 years
  3. compute_baseline() → avg_search_volume + q1..q4 seasonal factors
  4. UPSERT hiring_baseline ON CONFLICT (stock_id)

Key insight: Naver's 0–100 values are window-relative, but a single long window
(e.g. 3 years) keeps the monthly ratios mutually consistent, so the quarterly
factors (ratios of ratios) are scale-invariant — sidestepping the relative-value
contamination the daily snapshot approach would suffer.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from app.clients.naver_datalab_client import NaverDataLabClient
from app.collectors.hiring.keyword_generator import HiringKeywordGenerator

logger = logging.getLogger(__name__)

# hiring_baseline.q*_factor is NUMERIC(5,3) → max 99.999. Real seasonal factors
# never approach this; cap defensively so a degenerate ratio can't break the INSERT.
_FACTOR_MAX = 99.999


@dataclass(frozen=True)
class BaselineStats:
    avg_search_volume: float
    q1_factor: float
    q2_factor: float
    q3_factor: float
    q4_factor: float
    data_start_date: str | None
    data_end_date: str | None
    observations: int

    def factor(self, quarter: int) -> float:
        return getattr(self, f"q{quarter}_factor")


def compute_baseline(
    monthly_points: list[tuple[str, float]],
    *,
    min_months_per_quarter: int = 2,
) -> BaselineStats:
    """Aggregate a monthly search-ratio series into a seasonal baseline.

    Args:
        monthly_points: ``(period, ratio)`` pairs. ``period`` is a Naver DataLab
            month period like ``"2023-01-01"`` (or ``"2023-01"``); ``ratio`` is the
            0–100 search index for that month.
        min_months_per_quarter: a quarter observed fewer times than this keeps a
            neutral factor of 1.0 (not enough data to trust the seasonal signal).

    Returns:
        BaselineStats with ``avg_search_volume`` (overall mean) and ``qX_factor``
        = quarter_mean / overall_mean. Factors default to 1.0 (no seasonal
        correction) when overall mean is 0 or the quarter is under-observed.
    """
    parsed: list[tuple[date, float]] = []
    for period, ratio in monthly_points:
        observed = _parse_period(period)
        if observed is None or ratio is None:
            continue
        parsed.append((observed, float(ratio)))

    if not parsed:
        return BaselineStats(0.0, 1.0, 1.0, 1.0, 1.0, None, None, 0)

    overall = sum(r for _, r in parsed) / len(parsed)
    buckets: dict[int, list[float]] = {1: [], 2: [], 3: [], 4: []}
    for observed, ratio in parsed:
        quarter = (observed.month - 1) // 3 + 1
        buckets[quarter].append(ratio)

    def factor(quarter: int) -> float:
        values = buckets[quarter]
        if overall <= 0 or len(values) < min_months_per_quarter:
            return 1.0
        raw = (sum(values) / len(values)) / overall
        return round(min(raw, _FACTOR_MAX), 3)

    dates = sorted(observed for observed, _ in parsed)
    return BaselineStats(
        avg_search_volume=round(overall, 2),
        q1_factor=factor(1),
        q2_factor=factor(2),
        q3_factor=factor(3),
        q4_factor=factor(4),
        data_start_date=dates[0].isoformat(),
        data_end_date=dates[-1].isoformat(),
        observations=len(parsed),
    )


def _parse_period(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    text = str(value).strip()
    # Naver monthly periods come as "YYYY-MM-DD"; tolerate "YYYY-MM" too.
    if len(text) == 7:
        text = f"{text}-01"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


class DataLabBaselineCollector:
    """Builds and UPSERTs per-stock hiring baselines from DataLab search trends."""

    def __init__(
        self,
        *,
        pool: Any,
        client: NaverDataLabClient,
        years: int = 3,
        min_months_per_quarter: int = 2,
        keyword_generator: HiringKeywordGenerator | None = None,
    ) -> None:
        self._pool = pool
        self._client = client
        self._years = max(1, int(years))
        self._min_months_per_quarter = max(1, int(min_months_per_quarter))
        self._keywords = keyword_generator or HiringKeywordGenerator()

    def _date_window(self) -> tuple[str, str]:
        end = date.today() - timedelta(days=1)
        try:
            start = end.replace(year=end.year - self._years, day=1)
        except ValueError:  # Feb 29 → use day=1 anyway
            start = date(end.year - self._years, end.month, 1)
        return start.isoformat(), end.isoformat()

    async def build(self, *, targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Build baselines for the given target stocks.

        Args:
            targets: dicts with ``stock_id``, ``company_name``, optional
                ``category`` and ``short_name``.

        Returns:
            One result dict per target: stock_id, status, observations, stats.
        """
        start, end = self._date_window()
        results: list[dict[str, Any]] = []

        for target in targets:
            stock_id = target["stock_id"]
            company_name = target["company_name"]
            group = self._keywords.generate_keyword_group(
                company_name,
                category=target.get("category") or "tech",
                short_name=target.get("short_name"),
            )
            group_name = group["groupName"]
            try:
                records = await self._client.search_grouped(
                    keyword_group=group_name,
                    keywords=group["keywords"],
                    start_date=start,
                    end_date=end,
                    time_unit="month",
                )
                stats = compute_baseline(
                    [(rec.period, rec.ratio) for rec in records],
                    min_months_per_quarter=self._min_months_per_quarter,
                )
                await self._upsert(stock_id=stock_id, group_name=group_name, stats=stats)
                logger.info(
                    "baseline %s (stock_id=%s): obs=%d avg=%.2f q=[%.3f,%.3f,%.3f,%.3f]",
                    company_name, stock_id, stats.observations, stats.avg_search_volume,
                    stats.q1_factor, stats.q2_factor, stats.q3_factor, stats.q4_factor,
                )
                results.append(
                    {
                        "stock_id": stock_id,
                        "company_name": company_name,
                        "status": "success",
                        "observations": stats.observations,
                        "stats": stats,
                    }
                )
            except Exception as exc:  # one stock's failure must not sink the batch
                logger.error("baseline build failed for %s (stock_id=%s): %s", company_name, stock_id, exc)
                results.append(
                    {
                        "stock_id": stock_id,
                        "company_name": company_name,
                        "status": "failed",
                        "error": str(exc),
                    }
                )
        return results

    async def _upsert(self, *, stock_id: int, group_name: str, stats: BaselineStats) -> None:
        start_date = date.fromisoformat(stats.data_start_date) if stats.data_start_date else None
        end_date = date.fromisoformat(stats.data_end_date) if stats.data_end_date else None
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO hiring_baseline (
                    stock_id, avg_search_volume,
                    q1_factor, q2_factor, q3_factor, q4_factor,
                    keyword_group_name, data_start_date, data_end_date, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
                ON CONFLICT (stock_id) DO UPDATE SET
                    avg_search_volume  = EXCLUDED.avg_search_volume,
                    q1_factor          = EXCLUDED.q1_factor,
                    q2_factor          = EXCLUDED.q2_factor,
                    q3_factor          = EXCLUDED.q3_factor,
                    q4_factor          = EXCLUDED.q4_factor,
                    keyword_group_name = EXCLUDED.keyword_group_name,
                    data_start_date    = EXCLUDED.data_start_date,
                    data_end_date      = EXCLUDED.data_end_date,
                    updated_at         = NOW()
                """,
                stock_id,
                stats.avg_search_volume,
                stats.q1_factor,
                stats.q2_factor,
                stats.q3_factor,
                stats.q4_factor,
                group_name,
                start_date,
                end_date,
            )


def resolve_naver_client(timeout_seconds: int = 15) -> NaverDataLabClient:
    """Build a Naver client, preferring the hiring-dedicated quota credentials.

    Falls back to the shared NAVER_CLIENT_ID/SECRET when the hiring-specific pair
    is not configured, so quota can be split without forcing extra config.
    """
    client_id = os.getenv("HIRING_DATALAB_CLIENT_ID") or os.getenv("NAVER_CLIENT_ID", "")
    client_secret = os.getenv("HIRING_DATALAB_CLIENT_SECRET") or os.getenv("NAVER_CLIENT_SECRET", "")
    return NaverDataLabClient(client_id=client_id, client_secret=client_secret, timeout_seconds=timeout_seconds)
