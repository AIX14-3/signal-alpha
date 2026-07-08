"""DART 세부변동내역 파서 — 임원·주요주주 특정증권등 소유상황보고서 본문에서 거래유형과
취득처분단가를 결정론적으로 추출한다.

공시 본문 ``document.xml`` 은 DART 전용 SGML(``<TABLE ACLASS="EXTRACTION">``, 데이터 셀
``<TU>``/``<TE>``)이며, 셀에 **의미 코드 속성**이 박혀 있다:
- ``AUNIT="RPT_RSN" AUNITVALUE="<코드>"`` — 보고사유(거래유형) + 코드
- ``ACODE="ACI_AMT2"`` — 취득/처분단가(주당 KRW)
- ``ACODE="MDF_STK_CNT"`` — 증감수량

코드로 추출하므로 컬럼 위치·표시텍스트 변형에 강하다. 거래유형·단가는 무료 구조화 API
(elestock/majorstock)에 없고 오직 본문에만 있다(실측 확인).

거래유형 정규화(실측 90문서): 01 장내매수·02 장내매도·31 신규선임·33 신규보고·34 증여·
35 수증·58 주식매수선택권·99 기타. 장외/상속은 텍스트 폴백으로 보강.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# RPT_RSN 코드 → 정규화 거래유형.
TRADE_TYPE_BY_CODE: dict[str, str] = {
    "01": "onmarket_buy",
    "02": "onmarket_sell",
    "31": "appointment",
    "33": "appointment",
    "34": "gift",
    "35": "gift_received",
    "58": "stock_option",
    "99": "other",
}
# 방향 신호로 인정하는 유형(진짜 시장 확신). 나머지(증여·상속·옵션·신규선임·혼재)는 중립.
MARKET_TRADE_TYPES = frozenset({"onmarket_buy", "onmarket_sell"})

_SECTION_MARKER = "세부변동내역"
_TR_RE = re.compile(r"<TR\b.*?</TR>", re.S | re.I)
_RPT_RSN_RE = re.compile(r'AUNIT="RPT_RSN"\s+AUNITVALUE="(\d+)"[^>]*>([^<]*)<', re.I)
_PRICE_RE = re.compile(r'ACODE="ACI_AMT2"[^>]*>([^<]*)<', re.I)


@dataclass(frozen=True)
class DetailRow:
    trade_type: str
    unit_price: float | None


def _trade_type(code: str, text: str) -> str:
    mapped = TRADE_TYPE_BY_CODE.get(code)
    if mapped:
        return mapped
    # 코드 미매핑 → 표시텍스트 폴백(장외/상속 등 코드 미수집분).
    if "장내매수" in text:
        return "onmarket_buy"
    if "장내매도" in text:
        return "onmarket_sell"
    if "장외" in text:
        return "offmarket"
    if "상속" in text:
        return "inheritance"
    if "수증" in text:
        return "gift_received"
    if "증여" in text:
        return "gift"
    if "매수선택권" in text or "스톡옵션" in text:
        return "stock_option"
    return "other"


def _clean_price(text: str) -> float | None:
    """취득/처분단가 셀 → 주당 KRW float. 괄호값(총액/비-주당)·비수치는 None.

    국내 주당가는 정수 원 단위라 마침표는 천단위 구분자로 간주해 제거한다(예 '80.400'→80400,
    '73,400(원)'은 괄호 없는 '73,400'→73400)."""
    t = (text or "").strip()
    if not t or "(" in t or ")" in t:
        return None
    t = t.replace("원", "").replace(",", "").replace(".", "").replace(" ", "").strip()
    if not t.isdigit():
        return None
    value = float(t)
    return value if value > 0 else None


def parse_detail_rows(document_text: str) -> list[DetailRow]:
    """공시 본문(디코드된 SGML 문자열)에서 세부변동내역 행들을 추출한다.

    문서 내 '세부변동내역' 표(들)의 TBODY 행마다 (거래유형, 단가)를 뽑는다. RPT_RSN 셀이 없는
    행(헤더·요약 등)은 건너뛴다. 파싱 실패/무-표 → 빈 리스트."""
    rows: list[DetailRow] = []
    if not document_text:
        return rows
    start = 0
    while True:
        idx = document_text.find(_SECTION_MARKER, start)
        if idx < 0:
            break
        start = idx + len(_SECTION_MARKER)
        segment = document_text[idx : idx + 20000]
        tb = segment.find("<TBODY>")
        if tb < 0:
            continue
        te = segment.find("</TBODY>", tb)
        tbody = segment[tb : te if te > 0 else tb + 15000]
        for tr in _TR_RE.findall(tbody):
            reason = _RPT_RSN_RE.search(tr)
            if reason is None:
                continue
            trade_type = _trade_type(reason.group(1), reason.group(2).strip())
            price_match = _PRICE_RE.search(tr)
            unit_price = _clean_price(price_match.group(1)) if price_match else None
            rows.append(DetailRow(trade_type=trade_type, unit_price=unit_price))
    return rows


def classify_report(rows: list[DetailRow]) -> tuple[str | None, float | None]:
    """세부변동내역 행들 → 보고서 단위 (trade_type, unit_price).

    - 단일 유형 → 그 유형(예 전부 장내매수 → onmarket_buy).
    - 시장매매가 매수·매도 혼재거나 시장+비시장 혼재 → 'mixed'(→ 정규화에서 중립).
    - 복수 비시장 유형 → 'other'.
    - 행 없음 → (None, None) (미enrich/무-표: B-lite shares_delta 거동 유지).
    단가는 시장매매 행의 첫 유효 단가(장내만 의미 있음)."""
    if not rows:
        return None, None
    distinct = {row.trade_type for row in rows}
    market_types = distinct & MARKET_TRADE_TYPES
    if len(distinct) == 1:
        trade_type = next(iter(distinct))
    elif market_types:
        trade_type = "mixed"  # 매수·매도 혼재 또는 시장+비시장 혼재 → 모호 → 중립 처리
    else:
        trade_type = "other"  # 복수 비시장 유형
    unit_price = next(
        (row.unit_price for row in rows if row.trade_type in MARKET_TRADE_TYPES and row.unit_price),
        None,
    )
    return trade_type, unit_price
