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
        # raw_rows: one row per posting (job_count=1 each, from base_collector).
        # _aggregate_to_daily converts to one row per day with summed job_count so
        # indicators.py can compute meaningful momentum (recent_avg vs prior_avg).
        # Without this step all values are 1.0 → momentum always 0.
        raw_rows = [_row(record, factors) for record in records]
        rows = _aggregate_to_daily(raw_rows)
        posting_count = sum(r.get("job_count") or 0 for r in raw_rows)
        latest = rows[0]["observed_date"] if rows else None
        sector_demand = await self._sector_demand(stock_id, as_of)
        metadata: dict[str, Any] = {
            "rows": rows,
            "as_of": as_of.isoformat(),
            "lookback_days": self._lookback_days,
            "count": len(rows),          # number of active days (indicators 기준)
            "posting_count": posting_count,  # total individual postings
            "source_name": "HIRING",
            "sector_demand": sector_demand,
        }
        return [
            RawEvidence(
                source="HIRING",
                stock_code=stock_code,
                title=f"{stock_code} 채용 공고 {posting_count}건 (최근 {self._lookback_days}일)",
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


def _aggregate_to_daily(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """개별 공고 행(job_count=1)을 일별 집계 행으로 변환.

    base_collector는 공고 1건당 1행(job_count=1)을 저장한다. indicators.py는
    일별 집계 데이터를 기대하므로, 그대로 넘기면 recent_avg ≈ prior_avg ≈ 1.0 →
    momentum_pct 항상 0 고착. 이 함수가 같은 날짜의 공고를 job_count 합산으로
    묶어 의미 있는 시계열 데이터로 변환한다.

    change_pct: 전일 대비 증감률을 날짜 정렬 후 계산해 채운다. 이를 통해
    indicators.py의 avg_change_pct 컴포넌트도 실제 신호를 만들 수 있다.

    job_title / tech_stack: 당일 공고 전체의 값을 누적 보존해 analyzer.py의
    키워드 서피싱(hiring_focus)이 집계 후에도 동작하게 한다.
    """
    if not rows:
        return []

    # 1. 날짜별 집계
    daily: dict[str, dict[str, Any]] = {}
    titles_by_day: dict[str, list[str]] = {}
    techs_by_day: dict[str, list[str]] = {}

    for row in rows:
        d = row.get("observed_date")
        if not d:
            continue
        d = str(d)[:10]

        if d not in daily:
            daily[d] = {
                "observed_date": d,
                "job_count": 0,
                "seasonal_factor": row.get("seasonal_factor") or 1.0,
                "change_pct": None,
                "source_url": row.get("source_url"),
            }
            titles_by_day[d] = []
            techs_by_day[d] = []

        daily[d]["job_count"] += int(row.get("job_count") or 1)

        if title := row.get("job_title"):
            titles_by_day[d].append(str(title))
        for tech in (row.get("tech_stack") or []):
            if tech:
                techs_by_day[d].append(str(tech))

    # 2. 날짜 오름차순 정렬 후 전일 대비 change_pct 계산
    sorted_dates = sorted(daily)
    for i in range(1, len(sorted_dates)):
        prev, curr = sorted_dates[i - 1], sorted_dates[i]
        prev_count = daily[prev]["job_count"]
        if prev_count > 0:
            daily[curr]["change_pct"] = round(
                (daily[curr]["job_count"] - prev_count) / prev_count * 100, 2
            )

    # 3. 내림차순(최신순) 반환 + 키워드 필드 첨부
    result = []
    for d in reversed(sorted_dates):
        row_out = dict(daily[d])
        row_out["job_title"] = titles_by_day[d][0] if titles_by_day[d] else None
        row_out["tech_stack"] = list(dict.fromkeys(techs_by_day[d]))  # 순서 보존 중복 제거
        result.append(row_out)

    return result


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
