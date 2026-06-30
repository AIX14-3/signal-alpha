from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.auth import (
    NOTICE,
    _subscription_active,
    get_current_user_optional,
)
from app.core.database import get_database_pool
from signal_alpha_data_access.backend import SignalRepository

router = APIRouter(prefix="/api/reports", tags=["reports"])

# 연결점 → score_breakdown 키 / signal_events.source_type 매핑.
# REPORT/PATENT 도 사용자 노출 소스에 포함한다. REPORT 는 점수(score_100)를 산출하지 않지만
# 방향(투자의견 컨센서스)·밸류에이션(목표주가)·발행 리포트 목록을 상세로 노출한다. 집계
# SOURCE_ORDER(DART/PRICE/REPORT/HIRING/PATENT/DATALAB)와 정렬한다.
ALL_SOURCES = ("price", "dart", "hiring", "datalab", "patent", "report")
PUBLIC_SOURCES = {"dart", "datalab"}  # 비회원 공개
# 대체데이터는 소스별 독립 점수(C안 Phase 2)라 score_breakdown 에 HIRING/DATALAB 가
# top-level 평탄 키로 들어온다(과거 ALTERNATIVE 중첩 폐기).
_SOURCE_TO_BREAKDOWN = {
    "price": "PRICE",
    "dart": "DART",
    "hiring": "HIRING",
    "datalab": "DATALAB",
    "patent": "PATENT",
    "report": "REPORT",
}
_SOURCE_TO_EVENT_TYPE = {
    "price": "PRICE",
    "dart": "DART",
    "hiring": "HIRING",
    "datalab": "DATALAB",
    "patent": "PATENT",
    "report": "REPORT",
}
# 메타러너 소스별 예측률(주가 BASE ⊕ 각 공공데이터). 통합(SRC)은 헤드라인(score/direction)으로
# 이미 노출되므로 여기선 주가 1 + 공공데이터 5 = 6개 per-source 만 사용자에게 따로 보여준다.
# 값은 final_signals.source_predictions(JSONB) 의 score_100(0-100, 발행 시 적재)·direction.
_PREDICTION_RATE_SOURCES = ("price", "dart", "datalab", "hiring", "patent", "report")
_PREDICTION_RATE_RUN_KEY = {
    "price": "SRC_PRICE",
    "dart": "SRC_DART",
    "datalab": "SRC_DATALAB",
    "hiring": "SRC_HIRING",
    "patent": "SRC_PATENT",
    "report": "SRC_REPORT",
}
_BLIND_NOTICE = "전체 리포트는 구독 시 열람할 수 있습니다. 비구독자는 DART·네이버 데이터만 확인할 수 있습니다."


@router.get("/{stock_code}")
async def get_report(
    stock_code: str,
    current_user: dict[str, Any] | None = Depends(get_current_user_optional),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        row = await SignalRepository(connection).get_current_by_ticker(stock_code)
        if row is None:
            raise _api_error(404, "REPORT_NOT_FOUND", "발행된 리포트가 없습니다.")
        row = dict(row)
        is_member = current_user is not None
        unlocked = False
        if is_member:
            unlocked = await _is_unlocked(connection, int(current_user["id"]))
    return _report_response(row, unlocked=unlocked, is_member=is_member)


@router.get("/{stock_code}/sources/{source}")
async def get_source_detail(
    stock_code: str,
    source: str,
    current_user: dict[str, Any] | None = Depends(get_current_user_optional),
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    source = source.lower()
    if source not in ALL_SOURCES:
        raise _api_error(404, "SOURCE_NOT_FOUND", "알 수 없는 소스입니다.")

    async with pool.acquire() as connection:
        row = await SignalRepository(connection).get_current_by_ticker(stock_code)
        if row is None:
            raise _api_error(404, "REPORT_NOT_FOUND", "발행된 리포트가 없습니다.")
        row = dict(row)
        is_member = current_user is not None
        unlocked = is_member and await _is_unlocked(connection, int(current_user["id"]))

        # 접근 통제: 공개 소스(dart/datalab)는 누구나. 그 외는 구독자만 열람.
        if source not in PUBLIC_SOURCES:
            if not is_member:
                raise _api_error(401, "MEMBERSHIP_REQUIRED", "로그인 후 열람할 수 있습니다.")
            if not unlocked:
                raise _api_error(
                    402,
                    "SUBSCRIPTION_REQUIRED",
                    "구독 시 전체 소스 상세를 열람할 수 있습니다.",
                )

        detail = await SignalRepository(connection).get_detail_by_id(int(row["id"]))

    breakdown = _json_object(row.get("score_breakdown"))
    block = _source_block(source, breakdown, locked=False)
    events = _json_array(dict(detail).get("signal_events")) if detail is not None else []
    event_type = _SOURCE_TO_EVENT_TYPE[source]
    items = [_evidence(e) for e in events if str(e.get("source_type", "")).upper() == event_type]

    # 분석 근거 서술(score_breakdown.{SRC}.narrative_points). PRICE 처럼 signal_events 가 없는
    # 소스도 이 불릿으로 근거를 노출한다(DART/REPORT 는 items 표로 노출). 저장된 서술도, 이벤트
    # 근거도 없는 소스(주가 등 문서 비기반)는 발행된 score_breakdown 필드에서 근거 불릿을 파생한다.
    narrative_points = _narrative_points(source, breakdown)
    if not narrative_points and not items:
        narrative_points = _derive_points(source, breakdown)

    return {
        "stock": _stock(row),
        "source": source,
        "direction": block.get("direction"),
        "score": block.get("score"),
        "data_status": block.get("data_status"),
        "summary": block.get("summary"),
        "narrative_points": narrative_points,
        # REPORT 는 밸류에이션 fact(목표주가/방법론 등)를 추가 노출. 그 외 소스는 None.
        "valuation": _report_valuation(breakdown) if source == "report" else None,
        "items": items,
        "notice": NOTICE,
    }


# ----- helpers -----


async def _is_unlocked(connection: Any, user_id: int) -> bool:
    """전체 리포트 열람은 활성 구독자만 가능하다."""
    return await _subscription_active(connection, user_id)


def _report_response(
    row: dict[str, Any],
    *,
    unlocked: bool,
    is_member: bool,
) -> dict[str, Any]:
    breakdown = _json_object(row.get("score_breakdown"))
    sources = [
        _source_block(s, breakdown, locked=(not unlocked and s not in PUBLIC_SOURCES))
        for s in ALL_SOURCES
    ]
    # 소스별 예측률(주가 BASE ⊕ 각 공공데이터) — 통합(헤드라인) 외에 사용자에게 따로 노출.
    predictions = _json_object(row.get("source_predictions"))
    prediction_rates = [
        _prediction_rate_block(
            s, predictions, locked=(not unlocked and s not in PUBLIC_SOURCES)
        )
        for s in _PREDICTION_RATE_SOURCES
    ]
    access: dict[str, Any] = {"unlocked": unlocked, "is_member": is_member}

    return {
        "stock": _stock(row),
        "report_version": {
            "final_signal_id": row["id"],
            "run_key": row.get("run_key"),
            "signal_date": _iso(row.get("signal_date")),
            "updated_at": _iso(row.get("published_at") or row.get("created_at")),
        },
        "direction": row.get("signal") if unlocked else None,
        "score": _number(row.get("final_score")) if unlocked else None,
        "alignment_rate": _alignment(row.get("confidence")) if unlocked else None,
        "source_agreement": row.get("source_agreement") if unlocked else None,
        "warning_level": row.get("warning_level") if unlocked else None,
        "data_status": "ok" if unlocked else "partial",
        "summary": row.get("summary") if unlocked else None,
        "sources": sources,
        "prediction_rates": prediction_rates,
        "access": access,
        "notice": NOTICE if unlocked else _BLIND_NOTICE,
    }


def _source_block(source: str, breakdown: dict[str, Any], *, locked: bool) -> dict[str, Any]:
    if locked:
        return {"source": source, "locked": True}
    detail = breakdown.get(_SOURCE_TO_BREAKDOWN[source])
    if not isinstance(detail, dict):
        detail = {}
    return {
        "source": source,
        "direction": detail.get("direction", "unknown"),
        "score": _number(detail.get("score_100", detail.get("score"))),
        "data_status": detail.get("data_status", "missing"),
        # 주가(PRICE)는 워커가 기계식 요약("… 방향 positive, 점수 +0.400.")만 남기는 경우가
        # 있어, 그 패턴일 때만 사람이 읽기 쉬운 문장으로 풀어 쓴다(LLM 요약이 있으면 그대로 둠).
        "summary": _humanize_price_summary(detail) if source == "price" else detail.get("summary"),
        "locked": False,
    }


def _narrative_points(source: str, breakdown: dict[str, Any]) -> list[str]:
    """score_breakdown.{SRC}.narrative_points 를 문자열 리스트로 정규화해 노출. 없으면 []."""
    detail = breakdown.get(_SOURCE_TO_BREAKDOWN[source])
    if not isinstance(detail, dict):
        return []
    points = detail.get("narrative_points")
    if not isinstance(points, list):
        return []
    return [str(p) for p in points if isinstance(p, str) and p.strip()]


# 기계식 위험 플래그 → 일반 사용자가 이해할 수 있는 한국어 설명. 미정의 플래그는 원문 노출.
_RISK_FLAG_KO = {
    "high_volatility": "최근 주가 변동성이 평소보다 커서 가격 등락 폭이 크고, 그만큼 신호의 불확실성도 높은 편입니다.",
    "stale_data": "최근 시세 데이터 일부가 지연되어 반영됐습니다 — 분석의 최신성이 다소 떨어질 수 있습니다.",
    "low_liquidity": "거래량이 적어(유동성이 낮아) 가격 신호의 신뢰도가 낮을 수 있습니다.",
    "insufficient_history": "분석에 활용할 과거 데이터가 충분하지 않습니다.",
    "missing_source": "해당 데이터가 아직 충분히 수집되지 않았습니다.",
}

# 기계식 PRICE 요약("… 방향 positive, 점수 +0.400.") 판별용.
_PRICE_TERSE_RE = re.compile(r"방향\s+\w+\s*,\s*점수")


def _humanize_price_summary(detail: dict[str, Any]) -> str | None:
    """주가 요약이 기계식 패턴일 때만 사람이 읽기 쉬운 문장으로 풀어 쓴다(앞의 날짜는 유지).
    그 외(LLM 서술 등)에는 손대지 않고 원문을 그대로 돌려준다."""
    summary = detail.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return summary if isinstance(summary, str) else None
    if not _PRICE_TERSE_RE.search(summary):
        return summary
    phrase = {
        "positive": "상승 쪽에 무게가 실리는 ‘긍정’",
        "negative": "하락 쪽에 무게가 실리는 ‘부정’",
        "neutral": "뚜렷한 방향이 없는 ‘중립’",
        "mixed": "신호가 엇갈리는 ‘혼조’",
    }.get(str(detail.get("direction") or "").lower(), "방향성 판단")
    date_match = re.match(r"\s*(\d{4}-\d{2}-\d{2})", summary)
    prefix = f"{date_match.group(1)} 기준 " if date_match else ""
    return f"{prefix}최근 약 6개월(120영업일)간 주가 흐름과 거래 수급을 종합한 결과 {phrase} 신호입니다."


def _derive_points(source: str, breakdown: dict[str, Any]) -> list[str]:
    """저장된 narrative_points/이벤트 근거가 모두 없는 소스(주가 등 문서 비기반)를 위해
    발행된 score_breakdown 필드(방향·AI 예측 점수·리스크 플래그)에서 사람이 읽기 쉬운 근거
    불릿을 파생한다. 새 값을 만들지 않고 발행된 판정을 자연어로 풀어 쓴다."""
    detail = breakdown.get(_SOURCE_TO_BREAKDOWN[source])
    if not isinstance(detail, dict):
        return []
    points: list[str] = []
    subject = "주가 흐름과 거래 수급이" if source == "price" else "수집된 데이터가"
    direction = str(detail.get("direction") or "").lower()
    if direction == "positive":
        points.append(f"{subject} 전반적으로 상승 쪽으로 기울어 방향을 ‘긍정’으로 판단했습니다.")
    elif direction == "negative":
        points.append(f"{subject} 전반적으로 하락 쪽으로 기울어 방향을 ‘부정’으로 판단했습니다.")
    elif direction == "neutral":
        points.append(f"{subject} 뚜렷한 방향성을 보이지 않아 ‘중립’으로 판단했습니다.")
    score_100 = _number(detail.get("score_100"))
    if score_100 is not None:
        if score_100 > 50:
            points.append(
                f"AI 예측 점수는 100점 만점에 {score_100}점으로, 중간값보다 높아 상승(긍정) 신호로 해석됩니다."
            )
        elif score_100 < 50:
            points.append(
                f"AI 예측 점수는 100점 만점에 {score_100}점으로, 중간값보다 낮아 하락(부정) 신호로 해석됩니다."
            )
        else:
            points.append(f"AI 예측 점수는 100점 만점에 {score_100}점으로 중립 수준입니다.")
    flags = detail.get("risk_flags")
    if isinstance(flags, list):
        for flag in flags:
            text = str(flag).strip()
            if text:
                points.append(_RISK_FLAG_KO.get(text, f"유의 사항: {text}"))
    return points


def _report_valuation(breakdown: dict[str, Any]) -> dict[str, Any] | None:
    """score_breakdown.REPORT.valuation(목표주가/방법론/배수 등)을 그대로 노출. 없으면 None."""
    report = breakdown.get("REPORT")
    if not isinstance(report, dict):
        return None
    valuation = report.get("valuation")
    return valuation if isinstance(valuation, dict) else None


def _prediction_rate_block(
    source: str, predictions: dict[str, Any], *, locked: bool
) -> dict[str, Any]:
    """소스별 예측률 1건 — 주가 BASE ⊕ 해당 공공데이터의 0-100 'AI 예측 점수' + 방향.

    비회원에게 공개 소스(dart/datalab) 외에는 잠금 표시(``locked``). 해당 소스 예측이 없으면
    (아티팩트/데이터 결측) ``missing`` 으로 노출한다.
    """
    if locked:
        return {"source": source, "locked": True}
    entry = predictions.get(_PREDICTION_RATE_RUN_KEY[source])
    if not isinstance(entry, dict):
        return {"source": source, "score": None, "direction": "unknown", "data_status": "missing", "locked": False}
    return {
        "source": source,
        "score": _number(entry.get("score_100")),
        "direction": entry.get("direction", "unknown"),
        "data_status": "ok" if entry.get("score_100") is not None else "missing",
        "locked": False,
    }


def _stock(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("stock_id"),
        "stock_code": row.get("ticker"),
        "stock_name": row.get("name"),
        "market": row.get("market"),
        "sector": row.get("sector"),
    }


def _evidence(event: dict[str, Any]) -> dict[str, Any]:
    return {
        "title": event.get("title"),
        "summary": event.get("summary"),
        "event_date": _iso(event.get("event_date")),
        "direction": event.get("signal_direction"),
        "impact_level": event.get("impact_level"),
        "evidence_url": event.get("evidence_url"),
        "source_name": event.get("source_name"),
        "is_official": event.get("is_official"),
    }


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_array(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, str) and value:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, list) else []
    return []


def _number(value: Any) -> float | int | None:
    if value is None:
        return None
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return int(num) if num.is_integer() else num


def _alignment(value: Any) -> float | None:
    num = _number(value)
    if num is None:
        return None
    return round(num / 100, 4)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return isoformat() if callable(isoformat) else str(value)


def _api_error(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})
