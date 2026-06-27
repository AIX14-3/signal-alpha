"""Load DART ownership/insider events for a stock into a RawEvidence (#546 Phase 3).

src_dart base 모델(임원·주요주주 지분변동 event-study)의 입력 행을 공급한다. datalab/hiring
로더와 동일 계약: ``metadata["rows"]`` 에 정형 행 리스트(피처 어셈블리 ``assemble_features`` 가
소비), ``report_date`` 가 known_at(PIT 게이트는 어셈블리가 강제). 저빈도 재무·임직원은 base 모델이
아니라 메타러너 피처(D1)라 이 로더가 아니라 return 채널에서 별도 로드한다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from app.schemas.evidence import RawEvidence


class DartEvidenceLoader:
    source = "DART"

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
        records = await self._repository.list_dart_ownership_events_by_stock(
            stock_id=stock_id,
            since_date=since,
        )
        rows = [_row(record) for record in records]
        latest = rows[0]["report_date"] if rows else None
        metadata: dict[str, Any] = {
            "rows": rows,
            "as_of": as_of.isoformat(),
            "lookback_days": self._lookback_days,
            "count": len(rows),
            "source_name": "DART",
        }
        return [
            RawEvidence(
                source="DART",
                stock_code=stock_code,
                title=f"{stock_code} DART 지분변동 이벤트 {len(rows)}건",
                content="",
                published_at=latest,
                metadata=metadata,
            )
        ]


def _row(record: Any) -> dict[str, Any]:
    report_date = record["report_date"]
    return {
        "stock_id": record["stock_id"],
        "corp_code": record["corp_code"],
        "rcept_no": record["rcept_no"],
        "report_date": report_date.isoformat() if report_date else None,
        "holder_name": record["holder_name"],
        "holder_type": record["holder_type"],
        "shares": _to_float(record["shares"]),
        "ratio": _to_float(record["ratio"]),
        "shares_delta": _to_float(record["shares_delta"]),
        "ratio_delta": _to_float(record["ratio_delta"]),
        "report_reason": record["report_reason"],
    }


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
