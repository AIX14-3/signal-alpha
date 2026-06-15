"""Load DataLab raw details for a stock's categories into a RawEvidence.

DataLab is collected by *category*, so a stock's search-trend evidence is the
union of its mapped categories (``datalab_category_stocks``), each carrying a
weight the analyzer uses to blend category trends into a stock-level signal.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.schemas.evidence import RawEvidence


class DataLabEvidenceLoader:
    source = "DATALAB"

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
        metadata: dict[str, Any] = {
            "rows": rows,
            "as_of": as_of.isoformat(),
            "lookback_days": self._lookback_days,
            "count": len(rows),
            "category_ids": category_ids,
            "source_name": "NAVER_DATALAB",
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
    }


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
