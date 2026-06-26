"""소스별 정형 피처 어셈블리 (#525 Phase 1, point-in-time).

학습형 메타러너 stacking(#525)은 각 소스 데이터를 소스별 base 모델/메타러너에 넣기 전에
**공통 인터페이스의 정형 피처**로 모은다. 이 모듈은 그 어셈블리 계층이다.

원칙:
- **D3 look-ahead 0**: ``known_at <= asof`` 인 행만 피처에 반영한다(검색일/공고일/리포트 발행일 기준).
  ``pit_rows`` 가 이 게이트를 강제한다. 시점을 알 수 없는(날짜 결측) 행은 보수적으로 제외.
- **재사용**: 판정/스코어가 아닌 순수 피처는 이미 각 소스의 ``compute_indicators`` /
  ``build_valuation_summary`` 가 산출한다(clock-free, as-of 입력). 여기서 중복 구현하지 않고
  PIT 게이트 + 평탄화(flatten)만 얹는다.
- **DB 비접촉**: ``contract_adapter`` 와 동일하게 행 dict 를 입력으로 받는 순수 함수 — 단위테스트 가능.
  DB 로더(``get_features(stock, asof)``)는 이 위에 Phase 3 에서 얹는다.

출력: ``{"datalab": {...}, "hiring": {...}, "report": {...}}`` — 각 값은 숫자(또는 결측 None) 피처
dict. base 모델(고빈도 소스) 입력 + 메타러너 피처(저빈도 Report)로 그대로 쓴다.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from typing import Any, Mapping, Sequence

from app.analyzers.datalab.indicators import compute_indicators as datalab_indicators
from app.analyzers.hiring.indicators import compute_indicators as hiring_indicators
from app.analyzers.report.valuation import build_valuation_summary

# 소스별 known_at(시점 확정) 컬럼 — 이 날짜 <= asof 인 행만 피처에 반영(D3).
KNOWN_AT: dict[str, str] = {
    "datalab": "observed_date",  # 검색일
    "hiring": "observed_date",  # 공고 관측일
    "report": "publish_date",  # 리포트 발행일
}

DEFAULT_LOOKBACK_DAYS = 30


def _as_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def pit_rows(
    rows: Sequence[Mapping[str, Any]],
    asof: date,
    *,
    date_key: str,
) -> list[dict]:
    """``known_at(date_key) <= asof`` 인 행만 남긴다 (look-ahead 0, D3).

    날짜를 파싱할 수 없는 행은 시점을 확정할 수 없으므로 **제외**한다(보수적 PIT).
    """
    kept: list[dict] = []
    for row in rows:
        known_at = _as_date(row.get(date_key))
        if known_at is not None and known_at <= asof:
            kept.append(dict(row))
    return kept


def _numeric(values: Mapping[str, Any]) -> dict[str, float | None]:
    """숫자(또는 결측 None) 피처만 남긴다. 문자열/리스트/튜플/dict(top_skills,
    latest_facts, methodology_mix …)는 base 모델 입력이 아니므로 제외."""
    out: dict[str, float | None] = {}
    for key, value in values.items():
        if value is None:
            out[key] = None
        elif isinstance(value, bool):
            out[key] = float(value)
        elif isinstance(value, (int, float)):
            out[key] = float(value)
    return out


def assemble_features(
    asof: date,
    *,
    datalab_rows: Sequence[Mapping[str, Any]] = (),
    hiring_rows: Sequence[Mapping[str, Any]] = (),
    report_facts: Sequence[Mapping[str, Any]] = (),
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    sector_demand: dict | None = None,
) -> dict[str, dict[str, float | None]]:
    """``asof`` 시점의 소스별 정형 피처를 PIT-안전하게 어셈블한다.

    각 소스 행을 ``known_at <= asof`` 로 거른 뒤 기존 순수 indicator 함수에 위임하고,
    숫자 피처만 평탄화해 돌려준다. 행이 0개여도(저빈도/결측 as-of) 각 indicator 가
    빈/None 피처를 안전하게 반환한다.
    """
    datalab = datalab_indicators(
        pit_rows(datalab_rows, asof, date_key=KNOWN_AT["datalab"]),
        as_of=asof,
        lookback_days=lookback_days,
    )
    hiring = hiring_indicators(
        pit_rows(hiring_rows, asof, date_key=KNOWN_AT["hiring"]),
        as_of=asof,
        lookback_days=lookback_days,
        sector_demand=sector_demand,
    )
    report = build_valuation_summary(
        pit_rows(report_facts, asof, date_key=KNOWN_AT["report"])
    )

    return {
        "datalab": _numeric(asdict(datalab)),
        "hiring": _numeric(asdict(hiring)),
        "report": _numeric(report),
    }
