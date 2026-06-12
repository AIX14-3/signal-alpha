"""팩터 순수 함수 검증 — 합성 데이터로 부호·PIT·룩어헤드 없음을 고정한다."""

from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from signal_core.quant.factors.price import lowvol_60, momentum_12_1, reversal_1m
from signal_core.quant.factors.quality import quality_margin, quality_margin_yoy


def make_panel(n_days: int = 300) -> pd.DataFrame:
    """A=꾸준한 상승(저변동), B=하락 후 급반등(고변동) 2종목 합성 패널."""
    dates = pd.bdate_range("2024-01-02", periods=n_days)
    rows = []
    for day_index, date in enumerate(dates):
        rows.append(
            {
                "trade_date": date,
                "ticker": "A",
                "close": 100.0 * (1.001 ** day_index),  # 일 +0.1% 저변동 상승
                "volume": 1000,
            }
        )
        # B: 전반부 하락, 마지막 21일 급등 (반전 패턴) + 변동성 큼
        if day_index < n_days - 21:
            b_close = 100.0 * (0.999 ** day_index) * (1 + 0.03 * ((-1) ** day_index))
        else:
            b_close = 60.0 * (1.02 ** (day_index - (n_days - 21)))
        rows.append({"trade_date": date, "ticker": "B", "close": b_close, "volume": 1000})
    return pd.DataFrame(rows)


class PriceFactorSignTest(unittest.TestCase):
    def setUp(self):
        self.panel = make_panel()

    def last_value(self, series: pd.Series, ticker: str) -> float:
        mask = self.panel["ticker"] == ticker
        return float(series[mask].iloc[-1])

    def test_momentum_prefers_steady_uptrend(self):
        factor = momentum_12_1(self.panel)
        self.assertGreater(self.last_value(factor, "A"), self.last_value(factor, "B"))

    def test_reversal_prefers_recent_loser(self):
        factor = reversal_1m(self.panel)
        # B는 마지막 21일 급등 → 반전 팩터는 B를 불리하게 본다
        self.assertGreater(self.last_value(factor, "A"), self.last_value(factor, "B"))

    def test_lowvol_prefers_low_volatility(self):
        factor = lowvol_60(self.panel)
        self.assertGreater(self.last_value(factor, "A"), self.last_value(factor, "B"))

    def test_halted_rows_do_not_poison_returns(self):
        panel = self.panel.copy()
        # 거래정지 인코딩: close=0 행 — inf/0division 없이 NaN으로 흡수돼야 함
        panel.loc[panel.index[100], "close"] = 0.0
        factor = lowvol_60(panel)
        self.assertFalse(np.isinf(factor.dropna()).any())


class NoLookaheadTest(unittest.TestCase):
    def test_factor_at_t_ignores_future_prices(self):
        panel = make_panel()
        cutoff = panel["trade_date"].sort_values().unique()[200]
        for factor_fn in (momentum_12_1, reversal_1m, lowvol_60):
            full = factor_fn(panel)
            mutated = panel.copy()
            mutated.loc[mutated["trade_date"] > cutoff, "close"] *= 7.7  # 미래 변조
            partial = factor_fn(mutated)
            mask = panel["trade_date"] <= cutoff
            pd.testing.assert_series_equal(
                full[mask], partial[mask], check_names=False,
                obj=f"{factor_fn.__name__} lookahead",
            )


class QualityPointInTimeTest(unittest.TestCase):
    def make_fundamentals(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                # A: 마진 10% → 20% 개선 (FY 기준 YoY)
                dict(ticker="A", bsns_year=2023, period_type="FY", fiscal_date="2023-12-31",
                     available_date="2024-03-15", revenue=100.0, operating_income=10.0),
                dict(ticker="A", bsns_year=2024, period_type="FY", fiscal_date="2024-12-31",
                     available_date="2025-03-14", revenue=100.0, operating_income=20.0),
                # B: 마진 15% → 5% 악화
                dict(ticker="B", bsns_year=2023, period_type="FY", fiscal_date="2023-12-31",
                     available_date="2024-03-15", revenue=100.0, operating_income=15.0),
                dict(ticker="B", bsns_year=2024, period_type="FY", fiscal_date="2024-12-31",
                     available_date="2025-03-14", revenue=100.0, operating_income=5.0),
            ]
        )

    def make_panel_around(self, dates: list[str]) -> pd.DataFrame:
        rows = [
            {"trade_date": pd.Timestamp(d), "ticker": t, "close": 100.0, "volume": 1}
            for d in dates
            for t in ("A", "B")
        ]
        return pd.DataFrame(rows)

    def test_report_invisible_before_available_date(self):
        panel = self.make_panel_around(["2025-03-13", "2025-03-14"])
        fund = self.make_fundamentals()
        margin = quality_margin(panel, fund)
        a_before = margin[(panel["ticker"] == "A") & (panel["trade_date"] == "2025-03-13")]
        a_after = margin[(panel["ticker"] == "A") & (panel["trade_date"] == "2025-03-14")]
        self.assertAlmostEqual(float(a_before.iloc[0]), 0.10)  # 공시 전: 2023 FY만 보임
        self.assertAlmostEqual(float(a_after.iloc[0]), 0.20)  # 공시 당일부터 2024 FY

    def test_yoy_compares_same_period_type(self):
        panel = self.make_panel_around(["2025-04-01"])
        fund = self.make_fundamentals()
        yoy = quality_margin_yoy(panel, fund)
        a = float(yoy[panel["ticker"] == "A"].iloc[0])
        b = float(yoy[panel["ticker"] == "B"].iloc[0])
        self.assertAlmostEqual(a, +0.10)
        self.assertAlmostEqual(b, -0.10)
        self.assertGreater(a, b)


if __name__ == "__main__":
    unittest.main()
