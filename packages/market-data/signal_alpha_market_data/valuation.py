"""밸류에이션 파생 계산 (순수 함수, I/O 없음).

PSR은 키움·토스 어느 API도 직접 주지 않으므로 파생 계산한다:

    PSR = 시가총액 ÷ 매출(TTM)

⚠️ 단위 정합성:
- 키움 시가총액(`price_snapshots.market_cap`)은 **억원** 단위.
- DART 매출(`dart/financials.py`의 `_to_krw_million`)은 **백만원(KRW_million)** 단위.
- 1 억원 = 100 백만원 → 시총을 백만원으로 환산한 뒤 매출로 나눈다.

PSR은 trailing 지표라 분자(시총)는 실시간, 분모(매출 TTM)는 분기 갱신이다.
이 함수는 호출 시점의 두 값으로 비율만 계산한다(캐싱/롤오버는 호출측 책임).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

from signal_alpha_market_data.contracts import ValuationMetrics

# 1 억원 = 100 백만원
EOK_TO_KRW_MILLION = Decimal(100)

# PSR 출력 소수 자릿수.
_PSR_QUANT = Decimal("0.0001")


def _to_decimal(value: Decimal | int | float | str | None) -> Decimal | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None


def compute_psr(
    market_cap_eok: Decimal | int | float | None,
    revenue_ttm_krw_million: Decimal | int | float | None,
) -> Decimal | None:
    """PSR = (시가총액[억원] × 100) ÷ 매출 TTM[백만원].

    분자/분모 중 하나라도 없거나 매출이 0 이하(영업중단·결측·음수 추출오류)면 None.
    """
    market_cap = _to_decimal(market_cap_eok)
    revenue = _to_decimal(revenue_ttm_krw_million)
    if market_cap is None or revenue is None:
        return None
    if revenue <= 0:
        return None
    market_cap_krw_million = market_cap * EOK_TO_KRW_MILLION
    try:
        return (market_cap_krw_million / revenue).quantize(_PSR_QUANT)
    except (InvalidOperation, ZeroDivisionError):
        return None


def build_valuation(
    *,
    ticker: str,
    per: Decimal | int | float | None = None,
    pbr: Decimal | int | float | None = None,
    eps: Decimal | int | float | None = None,
    bps: Decimal | int | float | None = None,
    roe: Decimal | int | float | None = None,
    roa: Decimal | int | float | None = None,
    market_cap_eok: Decimal | int | float | None = None,
    revenue_ttm_krw_million: Decimal | int | float | None = None,
) -> ValuationMetrics:
    """키움 직값(PER/PBR/EPS/BPS/ROE/ROA) 패스스루 + PSR 파생을 합쳐 DTO 생성."""
    return ValuationMetrics(
        ticker=ticker,
        per=_to_decimal(per),
        pbr=_to_decimal(pbr),
        psr=compute_psr(market_cap_eok, revenue_ttm_krw_million),
        eps=_to_decimal(eps),
        bps=_to_decimal(bps),
        roe=_to_decimal(roe),
        roa=_to_decimal(roa),
    )
