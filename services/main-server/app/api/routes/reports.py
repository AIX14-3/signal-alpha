from __future__ import annotations

import json
import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.api.routes.auth import NOTICE
from app.core.database import get_database_pool
from signal_alpha_data_access.backend import SignalRepository

router = APIRouter(prefix="/api/reports", tags=["reports"])

# 연결점 → score_breakdown 키 / signal_events.source_type 매핑.
# REPORT/PATENT 도 사용자 노출 소스에 포함한다. REPORT 는 점수(score_100)를 산출하지 않지만
# 방향(투자의견 컨센서스)·밸류에이션(목표주가)·발행 리포트 목록을 상세로 노출한다. 집계
# SOURCE_ORDER(DART/PRICE/REPORT/HIRING/PATENT/DATALAB)와 정렬한다.
ALL_SOURCES = ("price", "dart", "hiring", "datalab", "patent", "report")
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


@router.get("/{stock_code}")
async def get_report(
    stock_code: str,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    async with pool.acquire() as connection:
        row = await SignalRepository(connection).get_current_by_ticker(stock_code)
        if row is None:
            raise _api_error(404, "REPORT_NOT_FOUND", "발행된 리포트가 없습니다.")
        row = dict(row)
    return _report_response(row)


@router.get("/{stock_code}/sources/{source}")
async def get_source_detail(
    stock_code: str,
    source: str,
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
        detail = await SignalRepository(connection).get_detail_by_id(int(row["id"]))

    breakdown = _json_object(row.get("score_breakdown"))
    block = _source_block(source, breakdown)
    events = _json_array(dict(detail).get("signal_events")) if detail is not None else []
    event_type = _SOURCE_TO_EVENT_TYPE[source]
    items = [_evidence(e) for e in events if str(e.get("source_type", "")).upper() == event_type]

    # 분석 근거 서술(score_breakdown.{SRC}.narrative_points). 저장된(LLM) 서술이 있으면 우선.
    # 없을 때: DART 는 items(공시 이벤트)는 표로 노출되지만 LLM off 면 불릿이 비므로, 이미 로드한
    # items 에서 결정론 근거 불릿을 파생한다. 그 외 문서 비기반 소스(주가 등)는 발행된
    # score_breakdown 필드에서 파생한다.
    narrative_points = _narrative_points(source, breakdown)
    if not narrative_points:
        if source == "dart":
            narrative_points = _derive_dart_points(items, breakdown.get("DART") or {})
        elif source == "report":
            narrative_points = _derive_report_points(
                items, breakdown.get("REPORT") or {}, _report_valuation(breakdown)
            )
        # DART/REPORT 라도 결정론 파생이 비고 이벤트도 없으면(items 비어있음), 그 외 문서
        # 비기반 소스(주가 등)와 동일하게 발행 score_breakdown 필드에서 일반 불릿을 파생한다.
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


def _report_response(row: dict[str, Any]) -> dict[str, Any]:
    breakdown = _json_object(row.get("score_breakdown"))
    sources = [_source_block(s, breakdown) for s in ALL_SOURCES]
    # 소스별 예측률(주가 BASE ⊕ 각 공공데이터) — 통합(헤드라인) 외에 사용자에게 따로 노출.
    predictions = _json_object(row.get("source_predictions"))
    prediction_rates = [
        _prediction_rate_block(s, predictions) for s in _PREDICTION_RATE_SOURCES
    ]

    return {
        "stock": _stock(row),
        "report_version": {
            "final_signal_id": row["id"],
            "run_key": row.get("run_key"),
            "signal_date": _iso(row.get("signal_date")),
            "updated_at": _iso(row.get("published_at") or row.get("created_at")),
        },
        "direction": row.get("signal"),
        "score": _number(row.get("final_score")),
        "alignment_rate": _alignment(row.get("confidence")),
        "source_agreement": row.get("source_agreement"),
        "warning_level": row.get("warning_level"),
        "data_status": "ok",
        "summary": row.get("summary"),
        "sources": sources,
        "prediction_rates": prediction_rates,
        "notice": NOTICE,
    }


def _source_block(source: str, breakdown: dict[str, Any]) -> dict[str, Any]:
    detail = breakdown.get(_SOURCE_TO_BREAKDOWN[source])
    if not isinstance(detail, dict):
        detail = {}
    return {
        "source": source,
        "direction": detail.get("direction", "unknown"),
        "score": _number(detail.get("score_100", detail.get("score"))),
        "data_status": detail.get("data_status", "missing"),
        # 주가(PRICE)/공시(DART)는 워커가 기계식 요약만 남기는 경우가 있어, 그 패턴일 때만 사람이
        # 읽기 쉬운 문장으로 풀어 쓴다(LLM 요약 등 이미 자연어면 원문 그대로 둠).
        "summary": _humanize_summary(source, detail),
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
    "correction_disclosure": "정정 공시가 포함되어 있어 원공시와 함께 확인이 필요합니다.",
    "valuation_review_required": "일부 리포트는 밸류에이션 검토가 필요해 원문과 함께 확인이 권장됩니다.",
}

# 기계식 PRICE 요약("… 방향 positive, 점수 +0.400.") 판별용.
_PRICE_TERSE_RE = re.compile(r"방향\s+\w+\s*,\s*점수")
# 기계식 DART 요약("DART 공시 N건 피처 산출 … 판정은 학습형 메타러너가 수행.") 판별용.
_DART_TERSE_RE = re.compile(r"피처\s*산출|학습형\s*메타러너")
# 기계식 REPORT 요약("… 밸류에이션 fact 기준 … 소스 간 일치도와 원문 근거 확인이 필요합니다.") 판별용.
_REPORT_TERSE_RE = re.compile(r"밸류에이션\s*fact|소스\s*간\s*일치도")


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


def _humanize_summary(source: str, detail: dict[str, Any]) -> str | None:
    """소스별 기계식 요약을 사람이 읽기 쉬운 문장으로 풀어 쓰는 디스패처(그 외엔 원문 통과)."""
    if source == "price":
        return _humanize_price_summary(detail)
    if source == "dart":
        return _humanize_dart_summary(detail)
    if source == "report":
        return _humanize_report_summary(detail)
    return detail.get("summary")


def _humanize_dart_summary(detail: dict[str, Any]) -> str | None:
    """DART 요약이 기계식 피처-산출 패턴일 때만 사람이 읽기 쉬운 문장으로 풀어 쓴다.
    그 외(LLM 서술 등 이미 자연어)에는 손대지 않고 원문을 그대로 돌려준다.

    DART 는 Phase 0 features-only 라 방향(direction)이 보통 'unknown' 이므로 방향을 단정하지
    않고, '판정은 학습형 모델이 수행' 사실 + 정정/검토 플래그만 정직하게 노출한다. 건수는 발행
    score_breakdown 의 값이 signal_events 와 어긋날 수 있어(stale) 헤드라인엔 넣지 않는다 —
    정확한 건수·분포는 상세의 분석 근거 불릿(_derive_dart_points)이 실제 이벤트에서 노출한다.
    """
    summary = detail.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return summary if isinstance(summary, str) else None
    if not _DART_TERSE_RE.search(summary):
        return summary
    text = (
        "최근 수집된 공시를 분석했습니다. 방향성 판정은 학습형 모델이 수행하며, "
        "개별 공시 내용은 아래 ‘근거 자료’에서 확인할 수 있습니다."
    )
    if _has_dart_review_flag(detail):
        text += " 정정·검토가 필요한 공시가 포함되어 있어 함께 확인이 필요합니다."
    return text


def _has_dart_review_flag(detail: dict[str, Any]) -> bool:
    """risk_flags 에 정정/검토 필요 플래그(correction_disclosure / review_required:*)가 있는지."""
    flags = detail.get("risk_flags")
    if not isinstance(flags, list):
        return False
    return any(
        str(f).strip() == "correction_disclosure" or str(f).strip().startswith("review_required")
        for f in flags
    )


def _humanize_report_summary(detail: dict[str, Any]) -> str | None:
    """REPORT 요약이 기계식 패턴일 때만 사람이 읽기 쉬운 문장으로 풀어 쓴다.
    그 외(LLM 서술 등 이미 자연어)에는 손대지 않고 원문을 그대로 돌려준다.

    구체적 수치(목표주가·방법론·배수)는 상세의 '밸류에이션' 카드와 분석 근거 불릿
    (_derive_report_points)이 노출하므로, 헤드라인은 무엇을 집계했는지만 안내한다."""
    summary = detail.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return summary if isinstance(summary, str) else None
    if not _REPORT_TERSE_RE.search(summary):
        return summary
    return (
        "증권사 리포트의 목표주가·밸류에이션 정보를 집계했습니다. 방향성 판정은 학습형 모델이 "
        "수행하며, 목표주가·방법론은 아래 ‘밸류에이션’ 카드와 ‘근거 자료’에서 확인할 수 있습니다."
    )


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


# DART 방향 라벨(공시 이벤트 signal_direction → 한국어).
_DART_DIRECTION_KO = {"positive": "긍정", "negative": "부정", "neutral": "중립"}


def _derive_dart_points(items: list[dict[str, Any]], detail: dict[str, Any]) -> list[str]:
    """DART 는 LLM 서술이 없어도 이미 로드한 공시 이벤트(items)에서 사람이 읽기 쉬운 근거
    불릿을 결정론적으로 파생한다(새 판정을 만들지 않고 수집된 공시 분포를 자연어로 요약).
    items 의 각 원소는 _evidence() 형태: direction / impact_level / title / event_date."""
    if not items:
        return []
    points: list[str] = []
    points.append(f"최근 공시 {len(items)}건을 분석했습니다.")

    direction_counts: dict[str, int] = {}
    for it in items:
        direction_counts[str(it.get("direction") or "unknown")] = (
            direction_counts.get(str(it.get("direction") or "unknown"), 0) + 1
        )
    parts = [
        f"{label} {direction_counts[key]}건"
        for key, label in _DART_DIRECTION_KO.items()
        if direction_counts.get(key)
    ]
    pending = direction_counts.get("unknown", 0)
    if pending:
        parts.append(f"분류 보류 {pending}건")
    if parts:
        points.append("이 중 " + "·".join(parts) + "으로 분류됐습니다.")

    high_impact = [it for it in items if str(it.get("impact_level") or "").lower() == "high"]
    if high_impact:
        points.append(f"중요도 높은 공시 {len(high_impact)}건이 포함되어 있습니다.")

    # 주목 공시(고임팩트 우선, 없으면 최신순) 최대 2건을 제목과 함께 인용. 같은 날 동일 제목
    # (예: 임원 소유상황보고서 다건)은 시각적으로 중복돼 보이므로 (날짜·제목) 기준 중복 제거.
    notable = high_impact or sorted(
        items, key=lambda it: str(it.get("event_date") or ""), reverse=True
    )
    seen: set[tuple[str, str]] = set()
    quoted = 0
    for it in notable:
        if quoted >= 2:
            break
        title = str(it.get("title") or "").strip()
        if not title:
            continue
        date_text = str(it.get("event_date") or "").strip()
        if (date_text, title) in seen:
            continue
        seen.add((date_text, title))
        prefix = f"[{date_text}] " if date_text else ""
        points.append(f"{prefix}{title}")
        quoted += 1

    if _has_dart_review_flag(detail):
        points.append("정정·검토가 필요한 공시가 포함되어 있어 함께 확인이 필요합니다.")
    return points


# 증권가 투자의견 방향(score_breakdown.REPORT.direction) → 한국어 컨센서스 문장.
_REPORT_CONSENSUS_KO = {
    "positive": "증권가의 투자의견은 대체로 긍정적입니다.",
    "negative": "증권가의 투자의견은 대체로 보수적입니다.",
    "neutral": "증권가의 투자의견은 중립적입니다.",
    "mixed": "증권가의 투자의견은 엇갈립니다.",
}


def _derive_report_points(
    items: list[dict[str, Any]], detail: dict[str, Any], valuation: dict[str, Any] | None
) -> list[str]:
    """REPORT 는 LLM 서술이 없어도 집계된 밸류에이션(목표주가/방법론/배수/비교기업)과 투자의견
    컨센서스에서 사람이 읽기 쉬운 근거 불릿을 결정론적으로 파생한다(새 판정 없이 사실만 요약)."""
    val = valuation if isinstance(valuation, dict) else {}
    points: list[str] = []

    count = _number(val.get("event_count")) or (len(items) if items else None)
    if count:
        points.append(f"증권사 리포트 {count}건을 집계했습니다.")

    consensus = _REPORT_CONSENSUS_KO.get(str(detail.get("direction") or "").lower())
    if consensus:
        points.append(consensus)

    target_price = _number(val.get("target_price"))
    if target_price:
        points.append(f"집계된 목표주가는 약 {target_price:,.0f}원입니다.")

    methodology = val.get("methodology")
    if isinstance(methodology, str) and methodology.strip() and methodology.strip().lower() != "unknown":
        points.append(f"주요 밸류에이션 방법론은 {methodology.strip()}입니다.")

    multiple = _number(val.get("implied_multiple_avg"))
    if multiple is None:
        multiple = _number(val.get("applied_multiple"))
    if multiple:
        # 정수면 "9배", 소수면 1자리로("86.9배") — 발행값의 과도한 소수점을 다듬는다.
        multiple_text = f"{multiple:.1f}".rstrip("0").rstrip(".")
        points.append(f"목표 배수는 평균 {multiple_text}배 수준입니다.")

    peers = val.get("peer_group")
    if isinstance(peers, list):
        names = [str(p).strip() for p in peers[:3] if str(p).strip()]
        if names:
            points.append("비교 기업: " + ", ".join(names) + ".")

    flags = detail.get("risk_flags")
    if isinstance(flags, list):
        for flag in flags:
            text = _RISK_FLAG_KO.get(str(flag).strip())
            if text and text not in points:
                points.append(text)
    return points


def _report_valuation(breakdown: dict[str, Any]) -> dict[str, Any] | None:
    """score_breakdown.REPORT.valuation(목표주가/방법론/배수 등)을 그대로 노출. 없으면 None."""
    report = breakdown.get("REPORT")
    if not isinstance(report, dict):
        return None
    valuation = report.get("valuation")
    return valuation if isinstance(valuation, dict) else None


def _prediction_rate_block(source: str, predictions: dict[str, Any]) -> dict[str, Any]:
    """소스별 예측률 1건 — 주가 BASE ⊕ 해당 공공데이터의 0-100 'AI 예측 점수' + 방향.

    해당 소스 예측이 없으면(아티팩트/데이터 결측) ``missing`` 으로 노출한다.
    """
    entry = predictions.get(_PREDICTION_RATE_RUN_KEY[source])
    if not isinstance(entry, dict):
        return {"source": source, "score": None, "direction": "unknown", "data_status": "missing"}
    return {
        "source": source,
        "score": _number(entry.get("score_100")),
        "direction": entry.get("direction", "unknown"),
        "data_status": "ok" if entry.get("score_100") is not None else "missing",
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
