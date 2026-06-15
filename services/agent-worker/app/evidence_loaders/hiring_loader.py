"""Load hiring raw details for a stock into a single RawEvidence."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from typing import Any

from app.analyzers.hiring.job_functions import aggregate_sector_demand
from app.schemas.evidence import RawEvidence


class HiringEvidenceLoader:
    source = "HIRING"

    def __init__(self, repository: Any, *, lookback_days: int) -> None:
        self._repository = repository
        self._lookback_days = lookback_days

    async def load(
        self,
        *,
        stock_id: int,
        stock_code: str,
        as_of: date,
    ) -> list[RawEvidence]:
        since = as_of - timedelta(days=self._lookback_days)
        records = await self._repository.list_hiring_details_by_stock(
            stock_id=stock_id,
            since_date=since,
        )
        factors = await self._seasonal_factors(stock_id)
        rows = [_row(record, factors) for record in records]
        latest = rows[0]["observed_date"] if rows else None
        sector_demand = await self._sector_demand(stock_id, as_of)
        metadata: dict[str, Any] = {
            "rows": rows,
            "as_of": as_of.isoformat(),
            "lookback_days": self._lookback_days,
            "count": len(rows),
            "source_name": "HIRING",
            "sector_demand": sector_demand,
        }
        return [
            RawEvidence(
                source="HIRING",
                stock_code=stock_code,
                title=f"{stock_code} 채용 공고 {len(rows)}건 (최근 {self._lookback_days}일)",
                content="",
                published_at=latest,
                metadata=metadata,
            )
        ]

    async def _seasonal_factors(self, stock_id: int) -> dict[int, float]:
        """Quarter → seasonal factor from hiring_baseline (defaults to 1.0).

        Graceful: any missing row / missing table / read error yields all-1.0
        factors, so the analyzer simply applies no seasonal correction.
        """
        getter = getattr(self._repository, "get_hiring_baseline", None)
        if getter is None:
            return _NEUTRAL_FACTORS
        try:
            baseline = await getter(stock_id)
        except Exception:
            return _NEUTRAL_FACTORS
        if not baseline:
            return _NEUTRAL_FACTORS
        return {
            1: _to_factor(baseline["q1_factor"]),
            2: _to_factor(baseline["q2_factor"]),
            3: _to_factor(baseline["q3_factor"]),
            4: _to_factor(baseline["q4_factor"]),
        }


    async def _sector_demand(self, stock_id: int, as_of: date) -> dict[str, Any] | None:
        """Peer job-function demand for this stock (C4), or None when unavailable.

        Graceful like ``_seasonal_factors``: a missing mapping, missing tables
        (migration 020 not applied), or any read error yields None, so the
        analyzer falls back to the pure own-momentum score. Returns None when the
        stock has no function mapping, so unseeded stocks are unaffected.
        """
        weights_getter = getattr(self._repository, "list_hiring_function_weights", None)
        rows_getter = getattr(self._repository, "list_recent_hiring_all_stocks", None)
        if weights_getter is None or rows_getter is None:
            return None
        try:
            weight_rows = await weights_getter(stock_id)
            function_weights = {
                str(r["function_key"]): float(r["weight"]) for r in weight_rows
            }
            if not function_weights:
                return None
            since = as_of - timedelta(days=self._lookback_days)
            peer_rows = await rows_getter(since_date=since)
        except Exception:
            return None
        rows = [
            {
                "stock_id": r["stock_id"],
                "keyword": r["keyword"],
                "job_count": r["job_count"],
                "observed_date": _observed_date(r["published_at"]),
            }
            for r in peer_rows
        ]
        demand = aggregate_sector_demand(
            rows,
            as_of=as_of,
            lookback_days=self._lookback_days,
            function_weights=function_weights,
            target_stock_id=stock_id,
        )
        if demand.momentum_pct is None:
            return None
        return demand.as_metadata()


_NEUTRAL_FACTORS: dict[int, float] = {1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0}


def _row(record: Any, factors: dict[int, float]) -> dict[str, Any]:
    published_at = record["published_at"]
    observed_date = _observed_date(published_at)
    payload = _payload(record)
    return {
        "keyword": record["keyword"],
        "job_category": record["job_category"],
        "job_count": _to_int(record["job_count"]),
        "previous_job_count": _to_int(record["previous_job_count"]),
        "change_pct": _to_float(record["change_pct"]),
        "observed_date": observed_date,
        "seasonal_factor": _factor_for(observed_date, factors),
        "source_url": record["source_url"],
        # Descriptive fields for the analyzer's keyword/tech surfacing (no score
        # impact). Pulled from the posting's extra_payload, falling back to title.
        "job_title": _job_title(payload, record),
        "tech_stack": _tech_list(payload.get("tech_stack")),
    }


def _payload(record: Any) -> dict[str, Any]:
    """extra_payload as a dict — asyncpg may hand it back as a dict or JSON str."""
    raw = record["extra_payload"]
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _job_title(payload: dict[str, Any], record: Any) -> str | None:
    title = payload.get("job_title")
    if title:
        return str(title)
    return record["title"] or None


def _tech_list(value: Any) -> list[str]:
    if not value:
        return []
    if isinstance(value, str):
        return [t.strip() for t in value.split(",") if t.strip()]
    if isinstance(value, (list, tuple)):
        return [str(t).strip() for t in value if str(t).strip()]
    return []


def _factor_for(observed_date: str | None, factors: dict[int, float]) -> float:
    if not observed_date:
        return 1.0
    try:
        month = int(observed_date[5:7])
    except (ValueError, IndexError):
        return 1.0
    quarter = (month - 1) // 3 + 1
    return factors.get(quarter, 1.0)


def _to_factor(value: Any) -> float:
    try:
        factor = float(value)
    except (TypeError, ValueError):
        return 1.0
    return factor if factor > 0 else 1.0


def _observed_date(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.date().isoformat() if isinstance(value, datetime) else value.isoformat()
    return str(value)[:10]


def _to_int(value: Any) -> int | None:
    if value is None:
        return None
    return int(value)


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
