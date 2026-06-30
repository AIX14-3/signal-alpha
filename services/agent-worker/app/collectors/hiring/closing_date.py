"""closing_date.py
채용공고 마감일(raw 문자열) → 표준 파생 키 파서.

소스마다 마감일 표기가 제각각이다(사람인 ``D-22`` / 잡코리아 ``06/15(월) 마감`` /
자소설 ISO datetime / 카카오 ``~06/22`` / ``상시채용`` / 다수 회사 ``None``). 원본 그대로는
비교·정렬·표시가 안 되므로, 원본을 보존한 채 파생 3키만 만든다(비파괴 추가):

  closing_date_parsed   "YYYY-MM-DD" ISO date | None   ← 비교·정렬 가능한 절대 날짜
  closing_date_display  사람이 읽는 표기        | None
  is_always_open        상시/수시 채용 여부      bool

설계 원칙
- ``parse_closing_date`` 는 절대 예외를 던지지 않는다(total function). 어떤 입력(None·빈값·
  깨진 문자열)에도 안전한 폴백을 반환해, 마감일 파싱 실패가 수집을 중단시키지 않게 한다.
- '시점에 따라 변하는 값'(d_day, is_open, status)은 저장하지 않는다 — observed 시점에
  고정되는 절대 날짜만 저장(나중에 stale 되지 않도록).
- D-n / MM-DD 는 ``observed_date``(KST 달력 날짜)를 기준점으로 절대 날짜를 만든다.

원본 ``closing_date`` 키는 이 모듈이 건드리지 않는다(호출부가 보존). 향후 기존 데이터
백필 스크립트도 동일 결과를 위해 이 함수를 재사용한다.
"""

from __future__ import annotations

import datetime
import logging
import re

logger = logging.getLogger(__name__)

_ALWAYS_OPEN_DISPLAY = "상시채용"

# 상시/수시/채용시 마감 등 — 마감일이 없는 '상시' 표기.
_ALWAYS_OPEN_RE = re.compile(r"(상시|수시|채용\s*시)")
# ISO date/datetime: 앞 10자가 YYYY-MM-DD (자소설 tz datetime, 네이버·현대 date 포함).
_ISO_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})")
# D-n (사람인): "D-22", "D - 3", "마감 D-0" 등.
_DDAY_RE = re.compile(r"\bD\s*-\s*(\d+)", re.IGNORECASE)
# MM/DD (잡코리아·카카오·사람인 틸드): "06/15(월) 마감", "~06/22" 등 노이즈 무시하고 추출.
_MMDD_RE = re.compile(r"(\d{1,2})\s*/\s*(\d{1,2})")


def _result(parsed: str | None = None,
            display: str | None = None,
            always_open: bool = False) -> dict:
    return {
        "closing_date_parsed": parsed,
        "closing_date_display": display,
        "is_always_open": always_open,
    }


def parse_closing_date(raw: object, observed_date: datetime.date | None) -> dict:
    """원본 마감일 문자열 → 파생 3키 dict. 절대 예외를 던지지 않는다.

    raw 가 None/빈값/파싱불가면 ``closing_date_parsed=None`` 으로 폴백한다(원본은 display
    에만 보존). ``observed_date`` 는 D-n·MM/DD 의 기준점(KST 달력 날짜); 없으면 그 분기는
    건너뛰고 기타 처리한다.
    """
    try:
        return _parse(raw, observed_date)
    except Exception as exc:  # total: 어떤 입력에도 수집 흐름을 깨지 않는다
        logger.warning("closing_date 파싱 폴백(raw=%r): %s", raw, exc)
        display = raw.strip() if isinstance(raw, str) and raw.strip() else None
        return _result(display=display)


def _parse(raw: object, observed_date: datetime.date | None) -> dict:
    if raw is None:
        return _result()
    text = str(raw).strip()
    if not text:
        return _result()

    # 1) 상시/수시 — 마감일 없음(절대 날짜 부재).
    if _ALWAYS_OPEN_RE.search(text):
        return _result(display=_ALWAYS_OPEN_DISPLAY, always_open=True)

    # 2) ISO date / datetime → 앞 10자(YYYY-MM-DD).
    iso_match = _ISO_RE.match(text)
    if iso_match:
        parsed = _try_iso(iso_match.group(1))
        if parsed is not None:
            iso = parsed.isoformat()
            return _result(parsed=iso, display=iso)

    # 3) D-n (사람인) → observed_date + n일.
    dday_match = _DDAY_RE.search(text)
    if dday_match and observed_date is not None:
        parsed = observed_date + datetime.timedelta(days=int(dday_match.group(1)))
        iso = parsed.isoformat()
        return _result(parsed=iso, display=iso)

    # 4) MM/DD → observed_date 기준 연도추론.
    mmdd_match = _MMDD_RE.search(text)
    if mmdd_match and observed_date is not None:
        parsed = _mmdd_to_date(int(mmdd_match.group(1)), int(mmdd_match.group(2)), observed_date)
        if parsed is not None:
            iso = parsed.isoformat()
            return _result(parsed=iso, display=iso)

    # 5) 기타/파싱불가 — 원본만 display 로 보존.
    return _result(display=text)


def _try_iso(value: str) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(value)
    except ValueError:
        return None


def _mmdd_to_date(month: int, day: int, observed_date: datetime.date) -> datetime.date | None:
    """MM/DD + observed_date → 절대 날짜. 마감일이 관측일보다 앞서면(연말 경계) +1년.

    검증 경계: 01/05 + observed 2026-12-20 → 2027-01-05,
              12/30 + observed 2026-01-10 → 2026-12-30.
    """
    try:
        candidate = datetime.date(observed_date.year, month, day)
    except ValueError:
        return None
    if candidate < observed_date:
        try:
            candidate = datetime.date(observed_date.year + 1, month, day)
        except ValueError:  # 02/29 등 다음 해가 평년인 경우
            return None
    return candidate
