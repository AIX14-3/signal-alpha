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

from app.analyzers.dart.indicators import compute_indicators as dart_indicators
from app.analyzers.datalab.indicators import compute_indicators as datalab_indicators
from app.analyzers.hiring.indicators import compute_indicators as hiring_indicators
from app.analyzers.price import indicators as price_ind
from app.analyzers.report.valuation import build_valuation_summary

# 소스별 known_at(시점 확정) 컬럼 — 이 날짜 <= asof 인 행만 피처에 반영(D3).
KNOWN_AT: dict[str, str] = {
    "datalab": "observed_date",  # 검색일
    "hiring": "observed_date",  # 공고 관측일
    "report": "publish_date",  # 리포트 발행일
    "dart": "report_date",  # DART 이벤트 공시 접수일(=rcept_dt)
    "price": "trade_date",  # OHLCV 체결일
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


# 주가 BASE 모델 피처 — **스케일-프리(비율)** 만 사용. 전 종목 패널 풀링(D1)이므로 절대가/절대
# 거래량(sma·last_close·net 절대주수)은 종목 간 스케일이 달라 피처로 쓰면 누설/노이즈가 된다.
# 기존 price/indicators 의 순수 함수(sma/rsi/volume_z/volatility/streak)를 재사용하되 비율로 변환.
PRICE_FEATURE_KEYS: tuple[str, ...] = (
    "ret_5d",
    "ret_20d",
    "close_sma20_gap",
    "sma5_sma20_gap",
    "sma20_sma60_gap",
    "rsi14",
    "volume_z",
    "volatility20",
    "golden_cross",
    "dead_cross",
    "foreign_streak",
    "institution_streak",
    "foreign_flow_ratio20",
    "institution_flow_ratio20",
)


def _gap(numerator: float | None, denominator: float | None) -> float | None:
    """``numerator/denominator - 1`` (비율 갭). 분모 0/결측이면 None."""
    if numerator is None or not denominator:
        return None
    return numerator / denominator - 1.0


def _return_over(closes: list[float], window: int) -> float | None:
    """``window`` 세션 누적수익률. 과거가 부족하거나 기준가 0이면 None."""
    if len(closes) <= window:
        return None
    base = closes[-window - 1]
    return closes[-1] / base - 1.0 if base else None


def price_features(rows: Sequence[Mapping[str, Any]]) -> dict[str, float | None]:
    """OHLCV 행 → 스케일-프리 주가 피처 dict(항상 ``PRICE_FEATURE_KEYS`` 전체 키, 결측=None).

    행 0개(저빈도/결측)면 전부 None 으로 안전 반환(``feature_order`` 안정성). 입력 행은
    PIT 게이트(``trade_date <= asof``)를 통과한 것이어야 한다(어셈블러가 보장).
    """
    if not rows:
        return {key: None for key in PRICE_FEATURE_KEYS}
    closes = [float(row["close"]) for row in rows]
    volumes = [float(row["volume"]) for row in rows]
    foreign = [row.get("foreign_net") for row in rows]
    institution = [row.get("institution_net") for row in rows]

    sma5 = price_ind.sma(closes, 5)
    sma20 = price_ind.sma(closes, 20)
    sma60 = price_ind.sma(closes, 60)
    golden, dead = price_ind.crossed(closes, short=5, long=20)
    avg_volume20 = price_ind.sma(volumes, 20)
    traded20 = avg_volume20 * 20 if avg_volume20 else None
    foreign_sum = price_ind._net_sum(foreign)
    institution_sum = price_ind._net_sum(institution)

    return {
        "ret_5d": _return_over(closes, 5),
        "ret_20d": _return_over(closes, 20),
        "close_sma20_gap": _gap(closes[-1], sma20),
        "sma5_sma20_gap": _gap(sma5, sma20),
        "sma20_sma60_gap": _gap(sma20, sma60),
        "rsi14": price_ind.rsi(closes),
        "volume_z": price_ind.volume_zscore(volumes),
        "volatility20": price_ind.volatility(closes),
        "golden_cross": float(golden),
        "dead_cross": float(dead),
        "foreign_streak": float(price_ind.net_buy_streak(foreign)),
        "institution_streak": float(price_ind.net_buy_streak(institution)),
        "foreign_flow_ratio20": (foreign_sum / traded20) if (foreign_sum is not None and traded20) else None,
        "institution_flow_ratio20": (
            (institution_sum / traded20) if (institution_sum is not None and traded20) else None
        ),
    }


def assemble_features(
    asof: date,
    *,
    datalab_rows: Sequence[Mapping[str, Any]] = (),
    hiring_rows: Sequence[Mapping[str, Any]] = (),
    report_facts: Sequence[Mapping[str, Any]] = (),
    dart_rows: Sequence[Mapping[str, Any]] = (),
    price_rows: Sequence[Mapping[str, Any]] = (),
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
    dart = dart_indicators(
        pit_rows(dart_rows, asof, date_key=KNOWN_AT["dart"]),
        as_of=asof,
        lookback_days=lookback_days,
    )
    # 주가는 스케일-프리 비율 피처를 직접 산출(절대가 indicator 비사용). PIT 게이트는 trade_date.
    price = price_features(pit_rows(price_rows, asof, date_key=KNOWN_AT["price"]))

    return {
        "datalab": _numeric(asdict(datalab)),
        "hiring": _numeric(asdict(hiring)),
        "report": _numeric(report),
        "dart": _numeric(asdict(dart)),
        "price": price,
    }
