"""Load patent raw details for a stock into a single RawEvidence."""

from __future__ import annotations

import json
from datetime import date, timedelta
from typing import Any

from app.schemas.evidence import RawEvidence


class PatentEvidenceLoader:
    source = "PATENT"

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
        records = await self._repository.list_patent_details_by_stock(
            stock_id=stock_id,
            since_date=since,
        )
        rows = [_row(record) for record in records]
        latest = rows[0]["application_date"] if rows else None
        metadata: dict[str, Any] = {
            "rows": rows,
            "as_of": as_of.isoformat(),
            "lookback_days": self._lookback_days,
            "count": len(rows),
            "source_name": "KIPRIS",
        }
        return [
            RawEvidence(
                source="PATENT",
                stock_code=stock_code,
                title=f"{stock_code} 특허 출원 {len(rows)}건 (최근 {self._lookback_days}일)",
                content="",
                published_at=latest,
                metadata=metadata,
            )
        ]


def _row(record: Any) -> dict[str, Any]:
    application_date = record["application_date"]
    # llm_features is populated only for enriched patents (status 'success'); the
    # analyzer treats a missing/empty significance as "not enriched" and falls back.
    features = _features(record)
    return {
        "application_no": record["application_no"],
        "patent_title": record["patent_title"],
        "applicant_name": record["applicant_name"],
        "application_date": application_date.isoformat() if application_date else None,
        "tech_category": record["tech_category"],
        "is_new_category": bool(record["is_new_category"]),
        "source_url": record["source_url"],
        "llm_features": features,
        "significance": features.get("significance") if features else None,
    }


def _features(record: Any) -> dict[str, Any] | None:
    if record.get("llm_status") != "success":
        return None
    raw = record.get("llm_features")
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return None
    return raw if isinstance(raw, dict) else None
