"""Load DataLab raw details for a stock's categories into a RawEvidence.

DataLab is collected by *category*, so a stock's search-trend evidence is the
union of its mapped categories (``datalab_category_stocks``), each carrying a
weight the analyzer uses to blend category trends into a stock-level signal.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from app.schemas.evidence import RawEvidence


class DataLabEvidenceLoader:
    source = "DATALAB"

    def __init__(
        self, repository: Any, *, lookback_days: int, attention_window_days: int = 180
    ) -> None:
        self._repository = repository
        self._lookback_days = lookback_days
        # Wider window (calendar days) for the neutral attention_spike rolling-z.
        # Fetched separately so the directional ``rows`` (lookback_days) stay
        # byte-identical — widening the directional window would change features.
        self._attention_window_days = max(attention_window_days, lookback_days)

    async def load(
        self,
        *,
        stock_id: int,
        stock_code: str,
        as_of: date,
    ) -> list[RawEvidence]:
        categories = await self._repository.list_datalab_categories_for_stock(stock_id)
        weight_by_category = {
            int(category["category_id"]): float(category["weight"]) for category in categories
        }
        category_ids = list(weight_by_category)

        since = as_of - timedelta(days=self._lookback_days)
        records = (
            await self._repository.list_datalab_details_by_category(
                category_ids=category_ids,
                since_date=since,
            )
            if category_ids
            else []
        )
        rows = [_row(record, weight_by_category) for record in records]
        latest = rows[0]["observed_date"] if rows else None

        # Separate, wider PIT series for the neutral attention_spike rolling-z.
        attention_since = as_of - timedelta(days=self._attention_window_days)
        attention_records = (
            await self._repository.list_datalab_details_by_category(
                category_ids=category_ids,
                since_date=attention_since,
            )
            if category_ids
            else []
        )
        attention_series = _blend_daily_series(attention_records, weight_by_category)

        metadata: dict[str, Any] = {
            "rows": rows,
            "as_of": as_of.isoformat(),
            "lookback_days": self._lookback_days,
            "count": len(rows),
            "category_ids": category_ids,
            "source_name": "NAVER_DATALAB",
            "attention_series": attention_series,
        }
        return [
            RawEvidence(
                source="DATALAB",
                stock_code=stock_code,
                title=f"{stock_code} 검색 트렌드 {len(rows)}건 / 카테고리 {len(category_ids)}개",
                content="",
                published_at=latest,
                metadata=metadata,
            )
        ]


def _blend_daily_series(
    records: list[Any], weight_by_category: dict[int, float]
) -> list[list[Any]]:
    """Weight-blend search_index across categories per observed_date.

    Returns ``[[iso_date, value], ...]`` sorted ascending — a stock-level PIT
    search series for the attention rolling-z. Records with no date/index are
    skipped; a date's value is the category-weighted mean of that day's indices.
    """
    numer: dict[str, float] = defaultdict(float)
    denom: dict[str, float] = defaultdict(float)
    for record in records:
        observed_date = record["observed_date"]
        search_index = _to_float(record["search_index"])
        if observed_date is None or search_index is None:
            continue
        weight = weight_by_category.get(int(record["category_id"]), 1.0)
        key = observed_date.isoformat()
        numer[key] += search_index * weight
        denom[key] += weight
    return [[key, numer[key] / denom[key]] for key in sorted(numer) if denom[key] > 0]


def _row(record: Any, weight_by_category: dict[int, float]) -> dict[str, Any]:
    observed_date = record["observed_date"]
    category_id = int(record["category_id"])
    return {
        "category_id": category_id,
        "weight": weight_by_category.get(category_id, 1.0),
        "keyword": record["keyword"],
        "keyword_group": record["keyword_group"],
        "observed_date": observed_date.isoformat() if observed_date else None,
        "search_index": _to_float(record["search_index"]),
        "previous_search_index": _to_float(record["previous_search_index"]),
        "change_pct": _to_float(record["change_pct"]),
        "is_spike": bool(record["is_spike"]),
        "polarity": (record.get("polarity") if hasattr(record, "get") else None) or "demand",
        # provenance (003): who/how the polarity was tagged. Scope A does not change
        # scoring; the analyzer only uses this to record agent_results.llm_model.
        "polarity_source": (record.get("polarity_source") if hasattr(record, "get") else None)
        or "default",
        "polarity_model": (record.get("polarity_model") if hasattr(record, "get") else None),
    }


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
