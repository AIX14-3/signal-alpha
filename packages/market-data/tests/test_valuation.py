from __future__ import annotations

from decimal import Decimal

from signal_alpha_market_data import build_valuation, compute_psr
from signal_alpha_market_data.contracts import ValuationMetrics


class TestComputePsr:
    def test_basic_unit_conversion(self):
        # 시총 5,000억원 = 500,000 백만원, 매출 200,000 백만원 → PSR 2.5
        assert compute_psr(5000, 200000) == Decimal("2.5000")

    def test_samsung_like_scale(self):
        # 시총 4,000,000억원, 매출 TTM 300,000,000 백만원
        # (4,000,000 * 100) / 300,000,000 = 1.3333
        assert compute_psr(4_000_000, 300_000_000) == Decimal("1.3333")

    def test_decimal_inputs(self):
        assert compute_psr(Decimal("1000"), Decimal("50000")) == Decimal("2.0000")

    def test_none_market_cap_returns_none(self):
        assert compute_psr(None, 200000) is None

    def test_none_revenue_returns_none(self):
        assert compute_psr(5000, None) is None

    def test_zero_revenue_returns_none(self):
        assert compute_psr(5000, 0) is None

    def test_negative_revenue_returns_none(self):
        # 적자/추출오류로 음수 매출이 들어와도 PSR을 만들지 않는다
        assert compute_psr(5000, -100) is None

    def test_garbage_input_returns_none(self):
        assert compute_psr("n/a", 200000) is None


class TestBuildValuation:
    def test_passthrough_and_derived_psr(self):
        v = build_valuation(
            ticker="005930",
            per=Decimal("12.5"),
            pbr=Decimal("1.2"),
            eps=Decimal("6000"),
            market_cap_eok=5000,
            revenue_ttm_krw_million=200000,
        )
        assert isinstance(v, ValuationMetrics)
        assert v.ticker == "005930"
        assert v.per == Decimal("12.5")
        assert v.pbr == Decimal("1.2")
        assert v.eps == Decimal("6000")
        assert v.psr == Decimal("2.5000")

    def test_psr_none_when_revenue_missing(self):
        v = build_valuation(ticker="000660", per=Decimal("8.0"), market_cap_eok=5000)
        assert v.per == Decimal("8.0")
        assert v.psr is None
