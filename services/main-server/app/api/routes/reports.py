from __future__ import annotations

import datetime
import json
import random
import re
from typing import Any

import httpx
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
        # PATENT 는 최근 공개된 특허 목록 + 장기 출원 추이를 추가 노출. 그 외 소스는 None.
        "patent": _patent_detail(breakdown) if source == "patent" else None,
        # HIRING 은 최근 공고의 게시일·마감일을 추가 노출. 그 외 소스는 None.
        "hiring": _hiring_detail(breakdown) if source == "hiring" else None,
        "items": items,
        "notice": NOTICE,
    }


@router.get("/{stock_code}/timeline")
async def get_report_timeline(
    stock_code: str,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """종목의 근거 이벤트를 소스 교차 시간순으로 반환(Evidence Timeline).

    소스 상세(/sources/{source})가 특정 소스만 필터하는 것과 달리, 전 소스 signal_events 를
    한 축(event_date)으로 모아 준다. 리포트 전체 공개(#788)라 게이팅 없이 전 소스를 노출한다.
    """
    async with pool.acquire() as connection:
        row = await SignalRepository(connection).get_current_by_ticker(stock_code)
        if row is None:
            raise _api_error(404, "REPORT_NOT_FOUND", "발행된 리포트가 없습니다.")
        row = dict(row)
        detail = await SignalRepository(connection).get_detail_by_id(int(row["id"]))

    events = _json_array(dict(detail).get("signal_events")) if detail is not None else []
    items = [_evidence(e) for e in events]
    items.sort(key=lambda it: (it.get("event_date") or ""), reverse=True)
    return {"stock": _stock(row), "items": items, "notice": NOTICE}


_PRICE_TF = {"min": 130, "day": 180, "month": 36, "year": 10}


@router.get("/{stock_code}/prices")
async def get_report_prices(stock_code: str, tf: str = "day") -> dict[str, Any]:
    """리포트 상단 주가 차트용 시세 시계열. tf=min|day|month|year.

    Yahoo Finance(무키)에서 한국 종목 실시세를 서버사이드로 조회한다(005930.KS / .KQ). 조회
    실패 시 결정론 합성 시세로 폴백(is_demo=true)해 차트가 절대 비지 않게 한다.
    """
    tf = tf if tf in _PRICE_TF else "day"
    bars = await _yahoo_prices(stock_code, tf)
    is_demo = bars is None
    if is_demo:
        bars, _last, _chg, _pct = _demo_price_series(stock_code, tf)
    assert bars is not None
    last = int(round(bars[-1]["close"]))
    prev = int(round(bars[-2]["close"])) if len(bars) > 1 else last
    change = last - prev
    pct = round(change / prev * 100, 2) if prev else 0.0
    return {
        "stock_code": stock_code,
        "timeframe": tf,
        "currency": "KRW",
        "last_price": last,
        "change": change,
        "change_pct": pct,
        "is_demo": is_demo,
        "bars": bars,
    }


# 과거 유사 사례(S6) — 이 종목의 과거 발화 이벤트를 (소스·방향)으로 묶고, event_study_panel 의
# 실측 20일 forward/abnormal 수익률로 "이런 신호 뒤 주가가 어땠나"를 집계한다. 3건 미만 그룹은
# 표본 부족으로 제외. 임베딩 코사인 recall(signal_episodes)은 pgvector·에피소드 적재가 갖춰진
# 프로덕션에서 "유사"를 대체하는 v2 경로(응답 계약 동일). 지금은 결정론(같은 소스·방향).
_PRECEDENT_GROUPS_SQL = """
    SELECT
        e.source_type AS source_type,
        e.signal_direction AS direction,
        count(*) AS n,
        avg(p.fwd_return_20d) AS avg_fwd_return_20d,
        avg(p.abnormal_return_20d) AS avg_abnormal_return_20d,
        avg(CASE WHEN p.fwd_return_20d > 0 THEN 1.0 ELSE 0.0 END) AS win_rate
    FROM event_study_panel p
    JOIN signal_events e ON e.id = p.signal_event_id
    WHERE p.stock_id = $1 AND p.fwd_return_20d IS NOT NULL
    GROUP BY e.source_type, e.signal_direction
    HAVING count(*) >= 3
    ORDER BY count(*) DESC
"""
_PRECEDENT_RECENT_SQL = """
    SELECT
        e.source_type AS source_type,
        e.signal_direction AS direction,
        e.title AS title,
        p.asof_date AS asof_date,
        p.fwd_return_20d AS fwd_return_20d,
        p.abnormal_return_20d AS abnormal_return_20d
    FROM event_study_panel p
    JOIN signal_events e ON e.id = p.signal_event_id
    WHERE p.stock_id = $1 AND p.fwd_return_20d IS NOT NULL
    ORDER BY p.asof_date DESC, p.id DESC
    LIMIT 8
"""


@router.get("/{stock_code}/precedents")
async def get_report_precedents(
    stock_code: str,
    pool: Any = Depends(get_database_pool),
) -> dict[str, Any]:
    """과거 유사 사례 + 실측 결과(S6) — "이런 신호 뒤 주가가 어땠나".

    이 종목의 과거 발화 이벤트를 (소스·방향)으로 묶어 20일 실측 수익률·시장초과수익·승률을
    집계(groups)하고, 최근 개별 사례(recent)를 함께 준다. 전체 공개(#788). 표본이 없으면
    빈 목록(예: 이벤트스터디 패널 미적재 종목)이라 UI 는 "사례 없음"으로 처리한다.
    """
    async with pool.acquire() as connection:
        row = await SignalRepository(connection).get_current_by_ticker(stock_code)
        if row is None:
            raise _api_error(404, "REPORT_NOT_FOUND", "발행된 리포트가 없습니다.")
        row = dict(row)
        stock_id = int(row["stock_id"])
        groups = await connection.fetch(_PRECEDENT_GROUPS_SQL, stock_id)
        recent = await connection.fetch(_PRECEDENT_RECENT_SQL, stock_id)

    return {
        "stock": _stock(row),
        "horizon_days": 20,
        "groups": [_precedent_group(dict(g)) for g in groups],
        "recent": [_precedent_example(dict(r)) for r in recent],
        "notice": NOTICE,
    }


# ----- helpers -----


# tf → Yahoo (interval, range). 분=당일 인트라데이, 그 외는 일봉/주봉/월봉.
_YF_TF = {"min": ("5m", "1d"), "day": ("1d", "6mo"), "month": ("1wk", "2y"), "year": ("1mo", "10y")}
_YF_UA = "Mozilla/5.0 (compatible; SignalAlpha/1.0)"


async def _yahoo_prices(code: str, tf: str) -> list[dict[str, Any]] | None:
    """Yahoo Finance 실시세(한국). 코스피 .KS → 코스닥 .KQ 순서로 시도. 실패 시 None."""
    interval, rng = _YF_TF[tf]
    intraday = tf == "min"
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            for suffix in (".KS", ".KQ"):
                try:
                    resp = await client.get(
                        f"https://query1.finance.yahoo.com/v8/finance/chart/{code}{suffix}",
                        params={"interval": interval, "range": rng},
                        headers={"User-Agent": _YF_UA},
                    )
                    resp.raise_for_status()
                    result = resp.json()["chart"]["result"][0]
                    stamps = result.get("timestamp") or []
                    q = result["indicators"]["quote"][0]
                    o, h, low, c = q.get("open"), q.get("high"), q.get("low"), q.get("close")
                    bars: list[dict[str, Any]] = []
                    for i, t in enumerate(stamps):
                        vals = (o[i], h[i], low[i], c[i])
                        if any(v is None for v in vals):
                            continue
                        when = (
                            int(t)
                            if intraday
                            else datetime.datetime.fromtimestamp(t, datetime.timezone.utc)
                            .date()
                            .isoformat()
                        )
                        bars.append(
                            {"time": when, "open": round(o[i]), "high": round(h[i]), "low": round(low[i]), "close": round(c[i])}
                        )
                    if len(bars) >= 2:
                        return bars
                except Exception:
                    continue
    except Exception:
        return None
    return None


def _demo_price_series(ticker: str, tf: str) -> tuple[list[dict[str, Any]], int, int, float]:
    """종목코드로 시드된 결정론 합성 OHLCV(랜덤워크). 재시작해도 같은 종목은 같은 시세."""
    seed = sum((i + 1) * ord(c) for i, c in enumerate(ticker)) or 1
    rng = random.Random(seed * 1000 + {"min": 1, "day": 2, "month": 3, "year": 4}[tf])
    n = _PRICE_TF[tf]
    price = float(40000 + (seed * 137) % 60000)  # 40,000 ~ 100,000원 대
    now = datetime.datetime.now(datetime.timezone.utc)

    def label(i: int) -> Any:
        back = n - 1 - i
        if tf == "min":
            return int((now - datetime.timedelta(minutes=back)).timestamp())
        if tf == "day":
            return (now.date() - datetime.timedelta(days=back)).isoformat()
        if tf == "month":
            m = now.month - 1 - back
            y = now.year + (m // 12)
            return datetime.date(y, (m % 12) + 1, 1).isoformat()
        return datetime.date(now.year - back, 1, 1).isoformat()  # year

    bars: list[dict[str, Any]] = []
    drift = 0.0008 if tf in ("month", "year") else 0.0003
    for i in range(n):
        chg = rng.uniform(-0.02, 0.02) + drift
        open_ = price
        close = max(1000.0, price * (1 + chg))
        high = max(open_, close) * (1 + rng.uniform(0, 0.008))
        low = min(open_, close) * (1 - rng.uniform(0, 0.008))
        bars.append(
            {"time": label(i), "open": round(open_), "high": round(high), "low": round(low), "close": round(close)}
        )
        price = close

    last = int(bars[-1]["close"])
    prev = int(bars[-2]["close"]) if len(bars) > 1 else last
    change = last - prev
    pct = round(change / prev * 100, 2) if prev else 0.0
    return bars, last, change, pct


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
        # consensus_score(소스 간 방향 일치도, 0-100)는 근거 중심 리포트의 "소스 간 일치도"
        # 섹션 시각화에 쓰인다. source_agreement(HIGH/MEDIUM/LOW)와 짝.
        "consensus_score": _number(row.get("consensus_score")),
        "warning_level": row.get("warning_level"),
        "data_status": "ok",
        "summary": row.get("summary"),
        # 긍정/주의 한 줄 핵심 + 소스별 근거 배열(근거 중심 재구성 §5.3 3·4 섹션).
        "bull_point": row.get("bull_point"),
        "bear_point": row.get("bear_point"),
        "positive_evidence": _evidence_list(row.get("positive_evidence")),
        "caution_evidence": _evidence_list(row.get("caution_evidence")),
        "sources": sources,
        "prediction_rates": prediction_rates,
        # 리포트 전체 공개(#788) — 게이팅 없음. FE 하위호환용으로 항상 unlocked=True 를 노출.
        "access": {"unlocked": True, "is_member": False},
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


# 기계식 위험 플래그 → 일반 사용자가 이해할 수 있는 한국어 설명.
# 6개 분석기가 내보내는 값 전부를 덮는다(analyzers/*/rules.py 의 risk_flags.append 참조).
# 미정의 플래그는 화면에 영문 식별자가 그대로 노출되므로, 새 플래그를 만들면 여기도 함께 추가한다.
_RISK_FLAG_KO = {
    # 공통(여러 소스)
    "no_data": "아직 분석할 데이터가 모이지 않았습니다.",
    "stale_data": "최근 데이터 일부가 지연되어 반영됐습니다 — 분석의 최신성이 다소 떨어질 수 있습니다.",
    "insufficient_history": "분석에 활용할 과거 데이터가 충분하지 않습니다.",
    "short_history": "관측 기간이 짧아 추세를 단정하기 이릅니다.",
    "low_base": "비교 기준이 되는 과거 수치가 작아, 변화율이 실제보다 크게 보일 수 있습니다.",
    "missing_source": "해당 데이터가 아직 충분히 수집되지 않았습니다.",
    # 주가(PRICE)
    "high_volatility": "최근 주가 변동성이 평소보다 커서 가격 등락 폭이 크고, 그만큼 신호의 불확실성도 높은 편입니다.",
    "low_liquidity": "거래량이 적어(유동성이 낮아) 가격 신호의 신뢰도가 낮을 수 있습니다.",
    "volume_spike": "거래량이 평소보다 크게 늘었습니다 — 단기 급변 가능성을 함께 살펴보세요.",
    # 문구에 "매수"/"매도"가 들어가면 투자 권유로 읽힌다(copy-safety 가드가 부분문자열로 잡는다).
    # "과매도" 대신 "낙폭 과대" 처럼 사실 묘사만 남긴다.
    "overbought": "단기간에 많이 올라 과열로 보이는 구간입니다(기술적 지표 기준).",
    "oversold": "단기간에 많이 내려 낙폭이 큰 구간입니다(기술적 지표 기준).",
    # 공시(DART)
    "correction_disclosure": "정정 공시가 포함되어 있어 원공시와 함께 확인이 필요합니다.",
    # 검색량(DATALAB)
    "search_spike": "검색량이 평소보다 급증했습니다 — 관심이 몰린 이유를 함께 확인해 보세요.",
    "risk_search": "부정적 맥락의 검색어가 함께 늘었습니다 — 어떤 이슈인지 확인이 필요합니다.",
    # 채용(HIRING)
    "hiring_decline": "채용 공고가 이전보다 눈에 띄게 줄었습니다.",
    "no_active_postings": "현재 진행 중인 채용 공고가 없습니다.",
    # 특허(PATENT)
    "signal_expired": "가장 최근 특허 공개가 오래되어, 이 신호는 만료된 것으로 봅니다.",
    # 증권사 리포트(REPORT)
    "no_valuation_signal": "밸류에이션 수치를 추출할 만한 리포트가 없습니다.",
    "valuation_review_required": "일부 리포트는 밸류에이션 검토가 필요해 원문과 함께 확인이 권장됩니다.",
    "implied_multiple_missing": "리포트에서 적용 배수를 확인하지 못해 목표주가의 근거가 불완전합니다.",
    # 파이프라인 품질
    "failed_source": "해당 데이터를 분석하지 못해 이번 집계에서 제외했습니다.",
    "analyzer_error": "분석 중 오류가 발생해 이번 집계에서 제외했습니다.",
    "score_out_of_range": "점수 규약을 벗어난 값이라 이번 집계에서 제외했습니다.",
}

# 같은 뜻을 다른 코드로 두 번 알리는 조합. 문구를 하나로 맞추면 중복 제거에서 함께 접힌다.
# (DART 는 정정공시를 `correction_disclosure` 와 `review_required:correction` 둘 다로 표시한다.)
_CORRECTION_TEXT = _RISK_FLAG_KO["correction_disclosure"]

# DART 는 검토 필요 공시를 `review_required:{event_type}` 형태로 표시한다(접두형).
_REVIEW_REQUIRED_PREFIX = "review_required:"
_DART_EVENT_TYPE_KO = {
    "correction": "정정공시",
    "dart_disclosure": "일반공시",
    "major_disclosure": "주요사항보고",
    "material_event": "주요사항보고",
    "insider_ownership": "임원·주요주주 지분변동",
    "periodic_report": "정기보고서",
    "governance_report": "지배구조보고서",
    "treasury_disposal": "자기주식 처분",
    "disclosure": "공시",
}


def _risk_flag_ko(flag: Any) -> str:
    """위험 플래그 → 한국어 설명. 접두형(`review_required:{event_type}`)도 푼다.

    미정의 플래그를 영문 그대로 내보내면 화면에 `search_spike` 같은 식별자가 노출된다
    (사용자 신고). 알 수 없는 값은 최소한 문장 꼴로 감싸 내보낸다.
    """
    text = str(flag).strip()
    if not text:
        return ""
    known = _RISK_FLAG_KO.get(text)
    if known:
        return known
    if text == "review_required":  # DART LLM 경로가 접미사 없이 내보내는 경우
        return "확인이 필요한 공시가 있어 원문을 함께 보시는 것이 좋습니다."
    if text.startswith(_REVIEW_REQUIRED_PREFIX):
        event_type = text[len(_REVIEW_REQUIRED_PREFIX) :]
        if event_type == "correction":
            # `correction_disclosure` 와 같은 사실을 가리킨다 → 같은 문장으로 접는다.
            return _CORRECTION_TEXT
        label = _DART_EVENT_TYPE_KO.get(event_type, event_type)
        return f"{label} 중 확인이 필요한 항목이 있어 원문을 함께 보시는 것이 좋습니다."
    return "확인이 필요한 항목이 있습니다."


def _risk_flags_ko(flags: Any) -> list[str]:
    """플래그 목록 → 한국어 문장 목록. **중복 문장은 한 번만** 노출한다(순서 유지).

    같은 카드에 "정정 공시가 포함되어…" 가 두 번, 밸류에이션 검토 문구가 두 번 뜨는 일이 있었다
    (분석기가 이벤트마다 같은 플래그를 쌓고, 서로 다른 코드가 같은 뜻을 가리킨다).
    """
    if not isinstance(flags, list):
        return []
    seen: set[str] = set()
    texts: list[str] = []
    for flag in flags:
        text = _risk_flag_ko(flag)
        if text and text not in seen:
            seen.add(text)
            texts.append(text)
    return texts

# 기계식 PRICE 요약("… 방향 positive, 점수 +0.400.") 판별용.
_PRICE_TERSE_RE = re.compile(r"방향\s+\w+\s*,\s*점수")
# 기계식 DART 요약("DART 공시 N건 피처 산출 … 판정은 학습형 메타러너가 수행.") 판별용.
_DART_TERSE_RE = re.compile(r"피처\s*산출|학습형\s*메타러너")
# 기계식 REPORT 요약("… 밸류에이션 fact 기준 … 소스 간 일치도와 원문 근거 확인이 필요합니다.") 판별용.
_REPORT_TERSE_RE = re.compile(r"밸류에이션\s*fact|소스\s*간\s*일치도")
# 기계식 DATALAB 요약("… 검색 트렌드 382건 분석: 방향 positive, 점수 +0.569 (모멘텀 …)") 판별용.
_DATALAB_TERSE_RE = re.compile(r"검색\s*트렌드.*방향\s+\w+|스파이크\s")
# 기계식 PATENT 요약("… 최근 900일 공개 특허 13305건 분석: 방향 mixed, 점수 -0.067.") 판별용.
_PATENT_TERSE_RE = re.compile(r"공개\s*특허\s*\d+건\s*분석|출원\s*지표\s*산출")
# 기계식 HIRING 요약("활성 채용공고 4건(유효창 180일·대기업, 감쇠활동 1.66) → 방향 negative, 점수 -0.217.")
_HIRING_TERSE_RE = re.compile(r"활성\s*채용공고|유효창|감쇠활동|방향\s+\w+,\s*점수")
# 내부 테이블명이 그대로 노출되던 no-data 문구.
_HIRING_NO_DATA_RE = re.compile(r"hiring_raw_details|분석할\s*채용\s*데이터가\s*없습니다")


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
    }.get(str(detail.get("direction") or "").lower())
    prefix = _leading_date(summary)
    body = "최근 약 6개월(120영업일)간 주가 흐름과 거래 수급을 종합했습니다."
    if phrase:
        body = f"최근 약 6개월(120영업일)간 주가 흐름과 거래 수급을 종합한 결과 {phrase} 신호입니다."
    return f"{prefix}{body}"


_DIRECTION_PHRASE = {
    "positive": "긍정",
    "negative": "주의",
    "neutral": "중립",
    "mixed": "엇갈리는",
}


def _leading_date(summary: str) -> str:
    match = re.match(r"\s*(\d{4}-\d{2}-\d{2})", summary)
    return f"{match.group(1)} 기준 " if match else ""


def _direction_phrase(detail: dict[str, Any]) -> str | None:
    """방향 라벨. 알 수 없으면 None — 호출부가 방향 절을 통째로 빼서 비문을 막는다."""
    return _DIRECTION_PHRASE.get(str(detail.get("direction") or "").lower())


def _humanize_datalab_summary(detail: dict[str, Any]) -> str | None:
    """"… 방향 positive, 점수 +0.569 (모멘텀 +0.211, 스파이크 0.21)." 같은 기계식 요약을 풀어 쓴다."""
    summary = detail.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return summary if isinstance(summary, str) else None
    if not _DATALAB_TERSE_RE.search(summary):
        return summary
    phrase = _direction_phrase(detail)
    head = (
        f"최근 한 달간 이 종목의 검색 관심도를 살펴본 결과 {phrase} 방향으로 읽힙니다."
        if phrase
        else "최근 한 달간 이 종목의 검색 관심도를 살펴봤습니다."
    )
    return f"{_leading_date(summary)}{head} 검색량은 사람들의 관심이 어디로 쏠리는지를 보여 줍니다."


def _humanize_patent_summary(detail: dict[str, Any]) -> str | None:
    """"… 최근 900일 공개 특허 13305건 분석: 방향 mixed, 점수 -0.067." 을 풀어 쓴다."""
    summary = detail.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return summary if isinstance(summary, str) else None
    if not _PATENT_TERSE_RE.search(summary):
        return summary
    phrase = _direction_phrase(detail)
    head = (
        f"최근 공개된 특허의 양과 기술 분야를 살펴본 결과 연구개발 흐름은 {phrase} 방향으로 읽힙니다."
        if phrase
        else "최근 공개된 특허의 양과 기술 분야를 살펴봤습니다."
    )
    return f"{_leading_date(summary)}{head} 특허는 출원 후 약 18개월 뒤에 공개됩니다."


# 이 상태의 소스는 집계기의 등가중 평균에서 **빠진다**(aggregation/tasks.py `_aggregate`:
# available 은 failed 제외, 점수 평균은 no_signal 제외). missing 은 결과 행 자체가 없는 소스.
_EXCLUDED_STATUSES = {"no_signal", "missing", "failed"}

_SOURCE_LABEL_KO = {
    "dart": "공시",
    "price": "주가",
    "report": "증권사 리포트",
    "datalab": "검색량",
    "patent": "특허",
    "hiring": "채용",
}

# 소스별 "왜 점수에 못 들어갔나". 데이터가 아예 없는 것과 "있지만 방향을 못 가른 것"은 다르다.
_EXCLUSION_REASON_KO = {
    "dart": "최근 공시에서 방향을 가를 만한 내용을 찾지 못해",
    "price": "최근 시세 데이터가 충분하지 않아",
    "report": "밸류에이션을 추출할 만한 증권사 리포트가 없어",
    "datalab": "최근 검색량 데이터가 충분하지 않아",
    "patent": "최근 공개된 특허를 찾지 못해",
    "hiring": "최근 올라온 채용 공고를 찾지 못해",
}


def _object_particle(word: str) -> str:
    """받침 유무로 목적격 조사(을/를)를 고른다. "채용를" 같은 비문을 막는다."""
    if not word:
        return "를"
    last = word[-1]
    if not ("가" <= last <= "힣"):
        return "를"
    has_final_consonant = (ord(last) - 0xAC00) % 28 != 0
    return "을" if has_final_consonant else "를"


def _exclusion_note(source: str, detail: dict[str, Any]) -> str | None:
    """점수에 산입되지 않은 소스의 설명 문장. 산입된 소스는 None.

    "분석할 채용 데이터가 없습니다"로 끝내면 사용자는 그래서 종합 점수가 어떻게 나온 건지
    알 수 없다. 빠진 이유와 함께 **나머지 소스로 계산했다**는 사실까지 밝힌다.
    """
    status = str(detail.get("data_status") or "").lower()
    if status not in _EXCLUDED_STATUSES:
        return None
    reason = (
        "이 데이터를 분석하지 못해"
        if status == "failed"
        else _EXCLUSION_REASON_KO.get(source, "이 데이터를 확인하지 못해")
    )
    label = _SOURCE_LABEL_KO.get(source, source)
    return f"{reason} 이번 종합 점수는 {label}{_object_particle(label)} 빼고 나머지 소스로 계산했습니다."


def _humanize_hiring_summary(detail: dict[str, Any]) -> str | None:
    """채용 요약에서 내부 표현(테이블명·점수 코드)을 걷어 낸다."""
    summary = detail.get("summary")
    if not isinstance(summary, str) or not summary.strip():
        return summary if isinstance(summary, str) else None
    if _HIRING_NO_DATA_RE.search(summary):
        return "최근 올라온 채용 공고를 찾지 못했습니다."
    if not _HIRING_TERSE_RE.search(summary):
        return summary
    phrase = _direction_phrase(detail)
    head = (
        f"현재 올라와 있는 채용 공고의 수와 최근 변화를 살펴본 결과 채용 흐름은 {phrase} 방향으로 읽힙니다."
        if phrase
        else "현재 올라와 있는 채용 공고의 수와 최근 변화를 살펴봤습니다."
    )
    return f"{head} 채용은 회사가 사업을 넓히는지 줄이는지를 비춥니다."


def _humanize_summary(source: str, detail: dict[str, Any]) -> str | None:
    """소스별 기계식 요약을 사람이 읽기 쉬운 문장으로 풀어 쓰는 디스패처(그 외엔 원문 통과).

    점수에 산입되지 않은 소스는 무엇을 못 봤는지 + **그래서 종합 점수를 어떻게 냈는지**를
    함께 밝힌다. 데이터가 없다는 사실만 알리고 끝내면 종합 점수가 어디서 왔는지 알 수 없다.
    """
    note = _exclusion_note(source, detail)
    if note:
        return note
    if source == "price":
        return _humanize_price_summary(detail)
    if source == "datalab":
        return _humanize_datalab_summary(detail)
    if source == "patent":
        return _humanize_patent_summary(detail)
    if source == "hiring":
        return _humanize_hiring_summary(detail)
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
            text = _risk_flag_ko(flag)
            if text and text not in points:
                points.append(text)
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
            text = _risk_flag_ko(flag)
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


def _patent_detail(breakdown: dict[str, Any]) -> dict[str, Any] | None:
    """score_breakdown.PATENT.patent(최근 공개 특허 목록 + 장기 출원 추이)를 노출. 없으면 None.

    특허는 출원 후 ~18개월 뒤 공개되므로 두 날짜 축을 함께 보여준다:
    recent_publications=최근 *공개된* 특허(공개일 최신순), filing_trend=연도별 출원 건수.
    """
    patent = breakdown.get("PATENT")
    if not isinstance(patent, dict):
        return None
    block = patent.get("patent")
    if not isinstance(block, dict):
        return None
    recent = block.get("recent_publications")
    trend = block.get("filing_trend")
    return {
        "recent_publications": recent if isinstance(recent, list) else [],
        "filing_trend": trend if isinstance(trend, list) else [],
    }


def _hiring_detail(breakdown: dict[str, Any]) -> dict[str, Any] | None:
    """score_breakdown.HIRING.hiring(최근 공고의 게시일·마감일)을 노출. 없으면 None.

    공고는 게시 후 마감일까지만 유효하므로 두 날짜 축을 함께 보여준다:
    posting_date=게시일, closing_date=마감일(ISO, 없으면 None). ``is_always_open`` 이 참이면
    마감일 없는 상시채용이라 만료로 표시하면 안 된다. 경과일/만료 판정은 화면이 수행한다
    (서버가 미리 계산하면 응답 캐시 시점에 굳는다).
    """
    hiring = breakdown.get("HIRING")
    if not isinstance(hiring, dict):
        return None
    block = hiring.get("hiring")
    if not isinstance(block, dict):
        return None
    postings = block.get("postings")
    return {"postings": postings if isinstance(postings, list) else []}


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


def _pct(value: Any) -> float | None:
    """비율(0.362) → 퍼센트(36.2, 소수 1자리). None 은 그대로."""
    if value is None:
        return None
    return round(float(value) * 100, 1)


def _precedent_group(g: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": (str(g.get("source_type")).lower() if g.get("source_type") else None),
        "direction": g.get("direction"),
        "count": _number(g.get("n")),
        # 20일 실측 평균 수익률(%)·시장 대비 초과수익(%)·승률(fwd_return_20d>0 비율, %).
        "avg_return": _pct(g.get("avg_fwd_return_20d")),
        "avg_abnormal_return": _pct(g.get("avg_abnormal_return_20d")),
        "win_rate": _pct(g.get("win_rate")),
    }


def _precedent_example(r: dict[str, Any]) -> dict[str, Any]:
    return {
        "source": (str(r.get("source_type")).lower() if r.get("source_type") else None),
        "direction": r.get("direction"),
        "title": r.get("title"),
        "date": _iso(r.get("asof_date")),
        "fwd_return": _pct(r.get("fwd_return_20d")),
        "abnormal_return": _pct(r.get("abnormal_return_20d")),
    }


def _evidence_list(value: Any) -> list[dict[str, Any]]:
    """positive_evidence/caution_evidence(집계가 채우는 JSONB dict 배열)를 FE용으로 정규화.

    내부 식별자(agent_result_id/source_signal_event_ids 등)는 떼고, risk_flags 는
    ``_risk_flag_ko`` 로 사람이 읽을 수 있는 한국어 설명으로 바꾼다. 요약도 소스 카드와
    **같은 humanizer** 를 태워, 기계식 템플릿이 근거 카드에만 날것으로 남지 않게 한다.
    FE 가 dict 내부 구조를 몰라도 되게 {source, summary, risk_flags} 만 노출한다. str 원소
    (구버전)는 요약만 담는다."""
    items: list[dict[str, Any]] = []
    for entry in _json_array(value):
        if isinstance(entry, dict):
            source = entry.get("source")
            flags = entry.get("risk_flags")
            risk = _risk_flags_ko(flags)
            source_key = str(source).lower() if source else None
            summary = entry.get("summary")
            if source_key:
                # direction 이 있어야 "… 긍정 방향으로 읽힙니다" 같은 문장을 만들 수 있다.
                # (구버전 발행 행에는 없다 → humanizer 가 방향 없는 문장으로 폴백한다.)
                summary = _humanize_summary(
                    source_key,
                    {
                        "summary": summary,
                        "risk_flags": flags,
                        "direction": entry.get("direction"),
                        # 점수에서 빠진 소스는 그 사실과 이유를 문장으로 대체한다.
                        "data_status": entry.get("data_status"),
                    },
                )
            items.append({
                "source": source_key,
                "summary": summary,
                "risk_flags": risk,
            })
        elif isinstance(entry, str) and entry.strip():
            items.append({"source": None, "summary": entry, "risk_flags": []})
    return items


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
        # source_type(DART/REPORT/HIRING/PATENT/DATALAB) — 타임라인에서 소스 아이콘 매핑용(additive).
        "source_type": event.get("source_type"),
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
