from __future__ import annotations

import json
from collections import Counter, OrderedDict
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.routes.auth import NOTICE, get_current_user
from app.core.database import get_database_pool

# detail 응답 sources 순서. 대체데이터는 소스별 독립 점수(C안 Phase 2)라 worker
# score_breakdown 에 HIRING/PATENT/DATALAB 가 각 top-level 키로 들어온다(ALTERNATIVE 폐기).
SOURCE_ORDER = ("DART", "PRICE", "REPORT", "HIRING", "PATENT", "DATALAB")
# final_signals.run_key (소스별 발행 모델 B) → list 응답 score_breakdown.alternative 하위 키.
ALT_RUN_KEYS = ("hiring", "patent", "datalab")
_STATUS_PRIORITY = {"WARNING": 3, "CAUTION": 2, "NORMAL": 1}
_AGREEMENT_PRIORITY = {"LOW": 3, "MEDIUM": 2, "HIGH": 1}  # 보수적 = 낮은 합의 우선


def _to_direction(value: Any) -> str:
    """final_signals.signal(소문자) → 응답 대문자(POSITIVE/NEGATIVE/NEUTRAL/MIXED)."""
    return (value or "neutral").upper()

router = APIRouter(tags=["signals"])


@router.get("/signals/{ticker}")
async def get_current_signal(
    ticker: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    # 인증 필수 + 큐레이션 응답. 과거엔 무인증으로 dict(row)(=SELECT * api.signals_current)를
    # 그대로 반환해 final_score/ml_*/score_breakdown 등 내부 컬럼이 비회원에게 노출됐다.
    # /api/signals/by-stock/{stock_code} 와 동일한 보호·필드 투영을 적용한다.
    from signal_alpha_data_access.backend import SignalRepository

    async with pool.acquire() as connection:
        row = await SignalRepository(connection).get_current_by_ticker(ticker)

    if row is None:
        raise _api_error(404, "SIGNAL_NOT_FOUND", "시그널을 찾을 수 없습니다.")

    return _signal_by_stock_response(dict(row))


@router.get("/api/signals/by-stock/{stock_code}")
async def get_signal_by_stock(
    stock_code: str,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    from signal_alpha_data_access.backend import SignalRepository

    async with pool.acquire() as connection:
        row = await SignalRepository(connection).get_current_by_ticker(stock_code)

    if row is None:
        raise _api_error(404, "SIGNAL_NOT_FOUND", "시그널을 찾을 수 없습니다.")

    return _signal_by_stock_response(dict(row))


@router.get("/api/signals/{signal_id}")
async def get_signal_detail(
    signal_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    from signal_alpha_data_access.backend import SignalRepository

    async with pool.acquire() as connection:
        row = await SignalRepository(connection).get_detail_by_id(signal_id)

    if row is None:
        raise _api_error(404, "SIGNAL_NOT_FOUND", "시그널을 찾을 수 없습니다.")

    return _signal_detail_response(dict(row))


@router.post("/api/signals/{signal_id}/read")
async def mark_signal_read(
    signal_id: int,
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    from signal_alpha_data_access.backend import SignalRepository, UserSignalRepository

    async with pool.acquire() as connection:
        signal = await SignalRepository(connection).get_detail_by_id(signal_id)
        if signal is None:
            raise _api_error(404, "SIGNAL_NOT_FOUND", "시그널을 찾을 수 없습니다.")
        read_state = await UserSignalRepository(connection).mark_signal_read(
            user_id=int(current_user["id"]),
            final_signal_id=int(signal["id"]),
        )

    return {
        "status": "read",
        "signal_id": signal_id,
        "read_at": _timestamp(read_state.get("read_at")),
        "read_date": _timestamp(read_state.get("read_date")),
        "notice": NOTICE,
    }


@router.get("/api/signals")
async def list_signals(
    stock_ids: str | None = Query(default=None, description="Comma-separated stock IDs (e.g. 1,2,3)"),
    current_user: dict[str, Any] = Depends(get_current_user),
    pool: Any = Depends(get_database_pool),
) -> list[dict[str, Any]]:
    """현재 발행 신호 목록을 종목별(Model A)로 런타임 그룹핑해 반환.

    DB는 소스별(run_key HIRING/PATENT/DATALAB) 다중 행(모델 B)으로 적재되지만, 본
    엔드포인트가 stock_id로 묶어 종목당 1개로 응집한다(스키마 변경 없음).
    """
    from signal_alpha_data_access.backend import SignalRepository

    parsed: list[int] = []
    if stock_ids is not None:
        parsed = [int(part) for part in (p.strip() for p in stock_ids.split(",")) if part.isdigit()]
        if not parsed:
            return []

    async with pool.acquire() as connection:
        repository = SignalRepository(connection)
        rows = (
            await repository.list_current_by_stock_ids(parsed)
            if parsed
            else await repository.list_current_published()
        )
        # 변화량(Δ)은 종목을 지목해 물었을 때만 구한다(관심종목 추적 화면). 브리핑처럼
        # 전 종목을 훑는 호출에는 불필요한 쿼리를 태우지 않는다.
        previous_rows = (
            await repository.list_previous_aggregated_by_stock_ids(parsed) if parsed else []
        )

    previous_by_stock = {int(dict(row)["stock_id"]): dict(row) for row in previous_rows}

    grouped: "OrderedDict[int, list[dict[str, Any]]]" = OrderedDict()
    for row in rows:
        record = dict(row)
        grouped.setdefault(record["stock_id"], []).append(record)

    return [
        _signal_list_item(stock_id, source_rows, previous=previous_by_stock.get(stock_id))
        for stock_id, source_rows in grouped.items()
    ]


def _recency_key(row: dict[str, Any]) -> tuple[Any, Any, Any]:
    """행의 최신도. 리포지토리 정렬과 같은 축(signal_date → published_at → created_at)."""
    return (
        row.get("signal_date") or date.min,
        row.get("published_at") or datetime.min.replace(tzinfo=UTC),
        row.get("created_at") or datetime.min.replace(tzinfo=UTC),
    )


def _latest_row_per_run_key(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """run_key 별 **가장 최신** 행만 남긴다.

    ``api.signals_current`` 는 과거 signal_date 행도 is_current=TRUE 로 들고 있어(리포지토리
    주석 참조) 한 종목에 같은 run_key 가 여러 날짜로 들어올 수 있다. 입력 순서에 기대어
    덮어쓰면 오래된 행이 남는다 — 최신도를 명시적으로 비교한다.
    """
    latest: dict[str, dict[str, Any]] = {}
    for row in rows:
        run_key = (row.get("run_key") or "").lower()
        if run_key not in ALT_RUN_KEYS:
            continue
        current = latest.get(run_key)
        if current is None or _recency_key(row) > _recency_key(current):
            latest[run_key] = row
    return latest


def _latest_aggregated_row(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """종목의 최신 AGGREGATED 행. 집계기가 6소스를 합쳐 낸 통합 신호다."""
    aggregated = [row for row in rows if (row.get("run_key") or "").upper() == "AGGREGATED"]
    return max(aggregated, key=_recency_key) if aggregated else None


def _sources_from_breakdown(breakdown: Any) -> dict[str, Any]:
    """AGGREGATED.score_breakdown → 6소스 {direction, score, data_status} 맵.

    worker breakdown 은 부호 점수 ``score``(-1~1)와 사용자 노출용 ``score_100``(0~100)을
    함께 싣는다. 노출은 ``score_100`` 을 쓴다(reports/dashboard 와 동일 규칙).
    """
    if isinstance(breakdown, str):
        try:
            breakdown = json.loads(breakdown)
        except json.JSONDecodeError:
            breakdown = None
    if not isinstance(breakdown, dict):
        breakdown = {}
    sources: dict[str, Any] = {}
    for source in SOURCE_ORDER:
        detail = breakdown.get(source)
        if not isinstance(detail, dict):
            sources[source.lower()] = {"direction": "UNKNOWN", "score": None, "data_status": "missing"}
            continue
        sources[source.lower()] = {
            "direction": _to_direction(detail.get("direction")),
            "score": _number(detail.get("score_100", detail.get("score"))),
            "data_status": detail.get("data_status", "ok"),
        }
    return sources


# 직전 발행본이 이보다 오래되면 변화량을 만들지 않는다. 2년 전 점수와 비교해 "변화 +12" 라고
# 쓰면 거짓말이다(실측: 어떤 종목의 직전 AGGREGATED 가 2023-12-13 이었다).
_MAX_DELTA_AGE_DAYS = 14


def _to_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def _change(row: dict[str, Any], previous: dict[str, Any] | None) -> dict[str, Any]:
    """현재 신호 대비 직전 발행본의 변화. 비교할 수 없으면 값들은 None 이다.

    ``score_delta`` 가 None 인 경우는 셋이다 — 직전 발행본이 없거나, 점수가 없거나,
    직전 발행이 너무 오래됐거나. 어느 쪽이든 화면은 "변화 없음" 이 아니라 "비교 불가" 로
    읽어야 하므로 ``previous_signal_date`` 를 함께 노출한다.
    """
    current_date = _to_date(row.get("signal_date"))
    previous_date = _to_date(previous.get("signal_date")) if previous else None
    change: dict[str, Any] = {
        "signal_date": current_date.isoformat() if current_date else None,
        "previous_signal_date": previous_date.isoformat() if previous_date else None,
        "previous_score": _number(previous.get("final_score")) if previous else None,
        "previous_direction": _to_direction(previous.get("signal")) if previous else None,
        "score_delta": None,
    }
    if previous is None or current_date is None or previous_date is None:
        return change
    if (current_date - previous_date).days > _MAX_DELTA_AGE_DAYS:
        return change  # 직전 발행이 너무 오래됨 → 변화량을 만들지 않는다.
    current_score = _number(row.get("final_score"))
    previous_score = change["previous_score"]
    if current_score is None or previous_score is None:
        return change
    change["score_delta"] = round(current_score - previous_score, 2)
    return change


def _aggregated_list_item(
    stock_id: int,
    base: dict[str, Any],
    row: dict[str, Any],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """통합 신호(AGGREGATED) 기반 카드 — 6소스가 모두 반영된 점수를 그대로 노출한다.

    이전에는 대체데이터 3소스(hiring/patent/datalab)의 소스별 발행 행만 평균해서
    DART·PRICE·REPORT 가 헤드라인 점수에 아예 들어가지 않았다. 집계기가 이미 6소스를
    등가중 평균해 final_score 를 내놓으므로(005930: 6소스 평균 → 48.35) 그 값을 쓴다.
    """
    consensus = row.get("consensus_score")
    if consensus is None:
        consensus = row.get("confidence")
    warning_level = row.get("warning_level") or "NORMAL"
    return {
        "stock_id": stock_id,
        "stock": {
            "id": stock_id,
            "stock_code": base.get("ticker"),
            "stock_name": base.get("name"),
            "market": base.get("market"),
        },
        "direction": _to_direction(row.get("signal")),
        "score": _number(row.get("final_score")),
        "alignment_rate": _alignment_rate(_number(consensus)),
        "source_agreement": row.get("source_agreement"),
        "warning_level": warning_level,
        "data_status": _overall_data_status(
            {"warning_level": warning_level, "needs_review": bool(row.get("needs_review", False))}
        ),
        "summary": row.get("summary") or base.get("summary"),
        "score_breakdown": {"sources": _sources_from_breakdown(row.get("score_breakdown"))},
        # 관심종목 추적 화면이 "무엇이 달라졌나" 로 정렬하기 위한 재료.
        "change": _change(row, previous),
    }


def _signal_list_item(
    stock_id: int,
    rows: list[dict[str, Any]],
    previous: dict[str, Any] | None = None,
) -> dict[str, Any]:
    base = rows[0]
    aggregated = _latest_aggregated_row(rows)
    if aggregated is not None:
        return _aggregated_list_item(stock_id, base, aggregated, previous)

    # 폴백: 아직 집계 전(소스별 발행만 있는) 종목. 있는 소스만으로 카드를 만든다.
    alternative: dict[str, Any] = {key: None for key in ALT_RUN_KEYS}
    scores: list[float] = []
    directions: list[str] = []
    warning_levels: list[str] = []
    agreements: list[str] = []
    consensus_values: list[float] = []
    needs_review = False

    for run_key, row in _latest_row_per_run_key(rows).items():
        signal = row.get("signal") or "neutral"
        alternative[run_key] = {"direction": _to_direction(signal), "score": _number(row.get("final_score"))}
        directions.append(signal)
        if row.get("final_score") is not None:
            scores.append(float(row["final_score"]))
        warning_levels.append(row.get("warning_level") or "NORMAL")
        if row.get("source_agreement"):
            agreements.append(row["source_agreement"])
        consensus = row.get("consensus_score")
        if consensus is None:
            consensus = row.get("confidence")
        if consensus is not None:
            consensus_values.append(float(consensus))
        needs_review = needs_review or bool(row.get("needs_review", False))

    warning_level = max(warning_levels, key=_STATUS_PRIORITY.get) if warning_levels else "NORMAL"
    avg_score = round(sum(scores) / len(scores), 2) if scores else None
    source_agreement = (
        max(agreements, key=lambda value: _AGREEMENT_PRIORITY.get(value, 0)) if agreements else None
    )
    avg_consensus = round(sum(consensus_values) / len(consensus_values), 2) if consensus_values else None
    return {
        "stock_id": stock_id,
        "stock": {
            "id": stock_id,
            "stock_code": base.get("ticker"),
            "stock_name": base.get("name"),
            "market": base.get("market"),
        },
        "direction": _to_direction(_determine_majority_direction(directions)),
        "score": _number(avg_score),
        "alignment_rate": _alignment_rate(avg_consensus),
        "source_agreement": source_agreement,
        "warning_level": warning_level,
        "data_status": _overall_data_status({"warning_level": warning_level, "needs_review": needs_review}),
        "summary": base.get("summary"),
        # 집계 전이라 6소스 breakdown 이 없다 — 발행된 소스만 채우고 나머지는 missing.
        "score_breakdown": {
            "sources": {
                source.lower(): (
                    {
                        "direction": alternative[source.lower()]["direction"],
                        "score": alternative[source.lower()]["score"],
                        "data_status": "ok",
                    }
                    if alternative.get(source.lower())
                    else {"direction": "UNKNOWN", "score": None, "data_status": "missing"}
                )
                for source in SOURCE_ORDER
            }
        },
        # 집계 전 종목은 비교 기준(AGGREGATED 직전 행)이 없다 — 계약은 같은 모양을 유지한다.
        "change": _change(base, None),
    }


def _ml_return(row: dict[str, Any]) -> dict[str, Any] | None:
    """메타러너 return 채널(#525 WS-C). 결정론 집계 점수와 별개 — 미산출 종목은 None."""
    score = _number(row.get("ml_final_score"))
    direction = row.get("ml_direction")
    if score is None and direction is None:
        return None
    return {
        "score": score,
        "direction": direction,
        "confidence": _number(row.get("ml_confidence")),
    }


def _signal_by_stock_response(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "signal_id": row["id"],
        "stock": {
            "id": row.get("stock_id"),
            "stock_code": row.get("ticker"),
            "stock_name": row.get("name"),
            "market": row.get("market"),
        },
        "direction": row.get("signal"),
        "score": _number(row.get("final_score")),
        "alignment_rate": _alignment_rate(row.get("consensus_score") or row.get("confidence")),
        "source_agreement": row.get("source_agreement"),
        "warning_level": row.get("warning_level"),
        "data_status": _overall_data_status(row),
        "needs_review": bool(row.get("needs_review", False)),
        "summary": row.get("summary"),
        "ml_return": _ml_return(row),
        "updated_at": _timestamp(row.get("published_at") or row.get("created_at")),
        "notice": NOTICE,
    }


def _determine_majority_direction(directions: list[str]) -> str:
    valid = [direction for direction in directions if direction]
    if not valid:
        return "neutral"
    ranked = Counter(valid).most_common(2)
    if len(ranked) > 1 and ranked[0][1] == ranked[1][1]:
        return "neutral"  # 동률 → 중립
    return ranked[0][0]


def _signal_detail_response(row: dict[str, Any]) -> dict[str, Any]:
    score_breakdown = _json_object(row.get("score_breakdown"))
    agent_results = _json_array(row.get("agent_results"))
    signal_events = _json_array(row.get("signal_events"))
    return {
        "signal_id": row["id"],
        "stock": {
            "id": row["stock_id"],
            "stock_code": row["ticker"],
            "stock_name": row["name"],
            "market": row.get("market"),
            "sector": row.get("sector"),
        },
        "direction": row["signal"],
        "score": _number(row.get("final_score")),
        "alignment_rate": _alignment_rate(row.get("consensus_score") or row.get("confidence")),
        "source_agreement": row.get("source_agreement"),
        "warning_level": row.get("warning_level"),
        "data_status": _overall_data_status(row),
        "needs_review": bool(row.get("needs_review", False)),
        "summary": row.get("summary"),
        "ml_return": _ml_return(row),
        "positive_evidence": _json_array(row.get("positive_evidence")),
        "caution_evidence": _json_array(row.get("caution_evidence")),
        "sources": [
            _source_response(
                source=source,
                detail=score_breakdown.get(source),
                agent_results=agent_results,
                signal_events=signal_events,
            )
            for source in SOURCE_ORDER
        ],
        "notice": NOTICE,
    }


def _source_response(
    *,
    source: str,
    detail: Any,
    agent_results: list[Any],
    signal_events: list[Any],
) -> dict[str, Any]:
    if not isinstance(detail, dict):
        detail = {}
    agent_result = _agent_result_for_source(
        agent_results=agent_results,
        source=source,
        agent_result_id=detail.get("agent_result_id"),
    )
    method_detail = _json_object(agent_result.get("method_detail")) if agent_result else {}
    return {
        "source": source,
        "direction": detail.get("direction", "unknown"),
        "score": _source_score(detail),
        "data_status": detail.get("data_status", "missing"),
        "needs_review": bool(detail.get("needs_review", detail.get("data_status") == "missing")),
        "summary": method_detail.get("summary") or detail.get("summary"),
        "risk_flags": _json_array(detail.get("risk_flags")),
        "evidence": _source_evidence(agent_result, source, signal_events),
    }


def _agent_result_for_source(
    *,
    agent_results: list[Any],
    source: str,
    agent_result_id: Any,
) -> dict[str, Any] | None:
    if agent_result_id is not None:
        for agent_result in agent_results:
            if isinstance(agent_result, dict) and agent_result.get("id") == agent_result_id:
                return agent_result
    for agent_result in agent_results:
        if not isinstance(agent_result, dict):
            continue
        method_detail = _json_object(agent_result.get("method_detail"))
        if _normalize_source(method_detail.get("source") or method_detail.get("source_type")) == source:
            return agent_result
    return None


def _source_evidence(
    agent_result: dict[str, Any] | None,
    source: str,
    signal_events: list[Any],
) -> list[dict[str, Any]]:
    event_ids = set(_int_list(agent_result.get("source_signal_event_ids") if agent_result else None))
    items: list[dict[str, Any]] = []
    for event in signal_events:
        if not isinstance(event, dict):
            continue
        if event_ids and int(event["id"]) not in event_ids:
            continue
        if not event_ids and _normalize_source(event.get("source_type")) != source:
            continue
        items.append(
            {
                "id": event.get("id"),
                "source_document_id": event.get("source_document_id"),
                "event_type": event.get("event_type"),
                "event_date": _timestamp(event.get("event_date")),
                "direction": event.get("signal_direction"),
                "impact_level": event.get("impact_level"),
                "title": event.get("title"),
                "summary": event.get("summary"),
                "evidence_url": event.get("evidence_url") or event.get("source_url"),
                "source_name": event.get("source_name"),
                "is_official": event.get("is_official"),
                "needs_review": bool(event.get("needs_review", False)),
            }
        )
    return items


def _overall_data_status(row: dict[str, Any]) -> str:
    if row.get("warning_level") == "WARNING":
        return "failed"
    if row.get("warning_level") == "CAUTION" or row.get("needs_review"):
        return "partial"
    return "ok"


def _source_score(detail: dict[str, Any]) -> float | int | None:
    score_100 = _number(detail.get("score_100"))
    if score_100 is not None:
        return score_100
    score = _number(detail.get("score"))
    if score is None:
        return None
    if -1 <= float(score) <= 1:
        return _number(round((float(score) + 1) * 50, 2))
    return score


def _alignment_rate(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return round(float(number) / 100, 4)


def _json_object(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return dict(value)


def _json_array(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    return list(value)


def _int_list(value: Any) -> list[int]:
    if value is None:
        return []
    if isinstance(value, list):
        return [int(item) for item in value]
    if isinstance(value, tuple):
        return [int(item) for item in value]
    if isinstance(value, str):
        parsed = json.loads(value)
        return [int(item) for item in parsed]
    return [int(value)]


def _normalize_source(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().upper()
    if text in SOURCE_ORDER:  # HIRING/PATENT/DATALAB 는 이제 각 독립 소스(C안 Phase 2)
        return text
    return None


def _number(value: Any) -> float | int | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        value = float(value)
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _timestamp(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
