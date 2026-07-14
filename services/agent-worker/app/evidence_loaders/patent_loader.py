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
        # 창은 공개일 기준(리포지토리가 COALESCE(publication_date, application_date)로
        # 필터). 특허는 출원 후 ~18개월 뒤 공개되므로 "최근 공개된 특허"를 잡으려면
        # 공개일을 이벤트 시점으로 써야 한다.
        since = as_of - timedelta(days=self._lookback_days)
        records = await self._repository.list_patent_details_by_stock(
            stock_id=stock_id,
            since_date=since,
        )
        rows = [_row(record) for record in records]
        # rows 는 이벤트(공개일 폴백 출원일) 최신순 → 첫 행이 가장 최근 공개.
        latest = rows[0]["event_date"] if rows else None
        # 장기 출원 추이(연도별 출원 건수) — 최근성 창과 무관한 전체 이력(맥락용).
        trend_records = await self._repository.patent_filing_trend_by_stock(stock_id=stock_id)
        filing_trend = [
            {"year": int(r["year"]), "count": int(r["count"])}
            for r in trend_records
            if r["year"] is not None
        ]
        metadata: dict[str, Any] = {
            "rows": rows,
            "as_of": as_of.isoformat(),
            "lookback_days": self._lookback_days,
            "count": len(rows),
            "filing_trend": filing_trend,
            "source_name": "KIPRIS",
        }
        return [
            RawEvidence(
                source="PATENT",
                stock_code=stock_code,
                title=f"{stock_code} 최근 공개 특허 {len(rows)}건 (최근 {self._lookback_days}일 공개 기준)",
                content="",
                published_at=latest,
                metadata=metadata,
            )
        ]


def _row(record: Any) -> dict[str, Any]:
    application_date = record["application_date"]
    # publication_date(공개일)는 신규 컬럼 — 리포지토리가 항상 SELECT(백필 전/미상
    # 행이나 stale prod 스키마에선 NULL). NULL 이면 event_date 가 출원일로 폴백한다.
    publication_date = record["publication_date"]
    event_date = publication_date or application_date
    # llm_features is populated only for enriched patents (status 'success'); the
    # analyzer treats a missing/empty significance as "not enriched" and falls back.
    features = _features(record)
    return {
        "application_no": record["application_no"],
        "patent_title": record["patent_title"],
        "applicant_name": record["applicant_name"],
        "application_date": application_date.isoformat() if application_date else None,
        "publication_date": publication_date.isoformat() if publication_date else None,
        "event_date": event_date.isoformat() if event_date else None,
        "tech_category": record["tech_category"],
        "is_new_category": bool(record["is_new_category"]),
        "source_url": record["source_url"],
        "url": _patent_url(record),
        "llm_features": features,
        "significance": features.get("significance") if features else None,
    }


def _patent_url(record: Any) -> str | None:
    """특허 원문 링크. BigQuery 적재분은 extra_payload.publication_number
    (예 ``KR-1020230012345-A``)를 갖고, Google Patents 는 하이픈 없는 공개번호를
    경로로 받는다 → 직링크 조립. 없으면 raw_documents.source_url 폴백(특허는 현재
    수집기가 안 채워 대개 NULL), 그것도 없으면 None(화면은 링크 없는 제목으로 렌더).
    """
    payload = record.get("extra_payload") if hasattr(record, "get") else None
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None
    if isinstance(payload, dict):
        publication_number = payload.get("publication_number")
        if isinstance(publication_number, str) and publication_number.strip():
            return f"https://patents.google.com/patent/{publication_number.replace('-', '')}"
    return record["source_url"] or None


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
