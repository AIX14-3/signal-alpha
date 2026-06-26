"""L6 event_study 단위테스트 — look-ahead 0 규율 + lift 게이트.

순수 계산 함수만 검증한다(DB 불필요). 핵심: 이벤트 당일 종가는 절대 base 로 쓰이지 않으며
(진입 = event_date 다음 영업일), 미래 윈도가 미경과면 라벨은 None 이다.
"""

import unittest
from datetime import date

from app.backtest.event_study import (
    EventStudyBuilder,
    PriceSeries,
    SignalEventRef,
    compute_abnormal_return,
    compute_forward_returns,
    compute_lift,
    forward_returns_from_entry,
    resolve_entry_index,
)


class ForwardReturnLookaheadTest(unittest.TestCase):
    def test_entry_is_session_strictly_after_event_date(self):
        dates = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
        # event_date = 1/5 (당일 세션 존재) → 진입은 1/6 (당일 종가 금지)
        self.assertEqual(resolve_entry_index(dates, date(2026, 1, 5)), 1)
        # event_date 가 마지막 세션 이후 → 진입 세션 없음
        self.assertIsNone(resolve_entry_index(dates, date(2026, 1, 7)))

    def test_event_day_close_is_never_the_base(self):
        # 이벤트 당일 종가에 인위적 스파이크(1000). 올바른 진입은 다음날(base=100)이어야 한다.
        dates = [date(2026, 1, 5), date(2026, 1, 6), date(2026, 1, 7)]
        closes = [1000.0, 100.0, 110.0]
        series = PriceSeries(dates, closes)

        asof_date, fwd = compute_forward_returns(series, date(2026, 1, 5), horizons=(1,))

        self.assertEqual(asof_date, date(2026, 1, 6))  # asof = 다음 영업일
        # fwd_1d = 110/100 − 1 = 0.10 (당일 종가 1000 미사용)
        self.assertAlmostEqual(fwd[1], 0.10)

    def test_insufficient_future_window_is_none(self):
        closes = [100.0, 101.0, 102.0]
        # 진입 = idx 1 (1/6). 1d 가능(idx2), 5d/20d 는 미래 부족 → None.
        fwd = forward_returns_from_entry(closes, entry_idx=1, horizons=(1, 5, 20))
        self.assertAlmostEqual(fwd[1], 102.0 / 101.0 - 1.0)
        self.assertIsNone(fwd[5])
        self.assertIsNone(fwd[20])

    def test_no_session_after_event_returns_all_none(self):
        series = PriceSeries([date(2026, 1, 5)], [100.0])
        asof_date, fwd = compute_forward_returns(series, date(2026, 1, 5), horizons=(1, 5))
        self.assertIsNone(asof_date)
        self.assertEqual(fwd, {1: None, 5: None})

    def test_zero_base_price_is_none(self):
        fwd = forward_returns_from_entry([0.0, 10.0], entry_idx=0, horizons=(1,))
        self.assertIsNone(fwd[1])


class AbnormalReturnTest(unittest.TestCase):
    def test_abnormal_is_stock_minus_benchmark(self):
        self.assertAlmostEqual(compute_abnormal_return(0.05, 0.02), 0.03)

    def test_abnormal_none_when_either_missing(self):
        self.assertIsNone(compute_abnormal_return(None, 0.02))
        self.assertIsNone(compute_abnormal_return(0.05, None))


class LiftGateTest(unittest.TestCase):
    def test_small_sample_is_gated(self):
        samples = [("catalyst", 0.05)] * 3 + [("baseline", 0.0)] * 3
        result = compute_lift(samples, treatment="catalyst")
        self.assertTrue(result["gated"])
        self.assertIsNone(result["lift"])
        self.assertIsNone(result["pvalue"])

    def test_lift_is_treatment_minus_baseline_mean(self):
        # 30 treatment @ +0.05, 30 control @ 0.0 → baseline mean 0.025, lift = 0.025.
        samples = [("catalyst", 0.05)] * 30 + [("control", 0.0)] * 30
        result = compute_lift(samples, treatment="catalyst", iters=200)
        self.assertFalse(result["gated"])
        self.assertAlmostEqual(result["lift"], 0.025, places=6)
        self.assertEqual(result["groups"]["catalyst"].n, 30)
        self.assertIsInstance(result["pvalue"], float)
        self.assertGreaterEqual(result["pvalue"], 0.0)
        self.assertLessEqual(result["pvalue"], 1.0)

    def test_none_and_nonfinite_returns_excluded(self):
        samples = [("a", None), ("a", float("inf")), ("a", 0.1), ("b", 0.2)]
        result = compute_lift(samples, treatment="a")
        self.assertEqual(result["baseline_n"], 2)  # None/inf 제외
        self.assertEqual(result["groups"]["a"].n, 1)


class BuildRowsTest(unittest.TestCase):
    def _series(self):
        dates = [date(2026, 1, d) for d in (5, 6, 7, 8, 9, 12)]
        return dates

    def test_build_rows_skips_missing_price_and_computes_abnormal(self):
        dates = self._series()
        stock_closes = [100.0, 100.0, 105.0, 105.0, 105.0, 110.0]  # 진입 1/6 base=100
        bench_closes = [100.0, 100.0, 102.0, 102.0, 102.0, 102.0]
        price_series = {10: PriceSeries(dates, stock_closes)}
        benchmark = PriceSeries(dates, bench_closes)

        events = [
            SignalEventRef(signal_event_id=1, stock_id=10, event_date=date(2026, 1, 5)),
            SignalEventRef(signal_event_id=2, stock_id=99, event_date=date(2026, 1, 5)),
        ]
        builder = EventStudyBuilder(horizons=(1, 5))
        rows = builder.build_rows(events, price_series, benchmark)

        self.assertEqual(len(rows), 1)  # stock 99 가격 없음 → 제외
        row = rows[0]
        self.assertEqual(row.signal_event_id, 1)
        self.assertEqual(row.asof_date, date(2026, 1, 6))
        self.assertAlmostEqual(row.fwd_return_1d, 105.0 / 100.0 - 1.0)
        self.assertEqual(row.universe_snapshot, "kospi20_seed")

    def test_abnormal_uses_horizon_20_only(self):
        # 20d 윈도가 없으면 abnormal 은 None(fwd_20d None → 종목수익 None).
        dates = self._series()
        price_series = {10: PriceSeries(dates, [100.0] * len(dates))}
        benchmark = PriceSeries(dates, [100.0] * len(dates))
        events = [SignalEventRef(1, 10, date(2026, 1, 5))]
        rows = EventStudyBuilder(horizons=(1, 5, 20)).build_rows(events, price_series, benchmark)
        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0].abnormal_return_20d)


if __name__ == "__main__":
    unittest.main()
