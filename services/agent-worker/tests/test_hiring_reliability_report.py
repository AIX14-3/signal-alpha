"""#298 hiring 신호 신뢰도 하니스 — 순수 지표 함수 단위테스트 (DB 무관).

flip율·저표본·보정 버킷팅·합성지수 등 핵심 지표를 합성 신호로 검증한다.
"""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "script"))

import hiring_signal_reliability_report as rr  # noqa: E402


def sig(stock_id, d, signal, *, score=50.0, agreement="MEDIUM", confidence=50.0,
        breakdown=None, caution=None):
    return {
        "stock_id": stock_id, "signal_date": date.fromisoformat(d), "signal": signal,
        "final_score": score, "confidence": confidence, "consensus_score": confidence,
        "source_agreement": agreement, "warning_level": "NORMAL", "needs_review": False,
        "caution_evidence": caution if caution is not None else [],
        "score_breakdown": breakdown if breakdown is not None else {"HIRING": 50},
    }


class FlipRateTest(unittest.TestCase):
    def test_counts_signal_flips_and_score_delta(self):
        signals = [
            sig(1, "2026-06-01", "positive", score=60),
            sig(1, "2026-06-02", "negative", score=40),  # flip
            sig(1, "2026-06-03", "negative", score=45),  # no flip
        ]
        f = rr.flip_rate(rr.per_stock_series(signals))
        self.assertEqual((f["flips"], f["pairs"]), (1, 2))
        self.assertAlmostEqual(f["rate"], 0.5)
        self.assertAlmostEqual(f["mean_score_delta"], (20 + 5) / 2)

    def test_single_point_is_unmeasurable(self):
        f = rr.flip_rate(rr.per_stock_series([sig(1, "2026-06-01", "neutral")]))
        self.assertEqual(f["pairs"], 0)
        self.assertIsNone(f["rate"])

    def test_per_stock_isolation(self):
        # 종목이 다르면 쌍을 만들지 않는다(서로 다른 시계열).
        signals = [sig(1, "2026-06-01", "positive"), sig(2, "2026-06-02", "negative")]
        self.assertEqual(rr.flip_rate(rr.per_stock_series(signals))["pairs"], 0)


class LowSampleTest(unittest.TestCase):
    def test_empty_breakdown_or_caution_token_counts_as_low(self):
        signals = [
            sig(1, "2026-06-01", "neutral", breakdown={}),                       # 빈 breakdown
            sig(1, "2026-06-02", "neutral", caution=["데이터 없음"]),             # 토큰
            sig(2, "2026-06-01", "positive", breakdown={"HIRING": 60}),          # 정상
        ]
        r = rr.low_sample_ratio(signals)
        self.assertEqual((r["low"], r["total"]), (2, 3))
        self.assertAlmostEqual(r["ratio"], 2 / 3)

    def test_empty_input(self):
        self.assertIsNone(rr.low_sample_ratio([])["ratio"])


class SingleSourceTest(unittest.TestCase):
    def test_ratio_ignores_empty_breakdown(self):
        signals = [
            sig(1, "2026-06-01", "positive", breakdown={"HIRING": 60}),           # 단일
            sig(2, "2026-06-01", "positive", breakdown={"HIRING": 60, "DART": 55}),  # 다중
            sig(3, "2026-06-01", "neutral", breakdown={}),                        # 웜업 → considered 제외
        ]
        r = rr.single_source_ratio(signals)
        self.assertEqual((r["single"], r["considered"]), (1, 2))
        self.assertAlmostEqual(r["ratio"], 0.5)


class CalibrationTest(unittest.TestCase):
    def _series(self, rows):
        return rr.per_stock_series(rows)

    def test_buckets_flip_by_prev_agreement(self):
        # HIGH 쌍은 안 뒤집힘, LOW 쌍은 뒤집힘 → 보정 정상(미반전).
        rows = [
            sig(1, "2026-06-01", "positive", agreement="HIGH"),
            sig(1, "2026-06-02", "positive", agreement="HIGH"),   # HIGH 쌍, no flip
            sig(2, "2026-06-01", "positive", agreement="LOW"),
            sig(2, "2026-06-02", "negative", agreement="LOW"),    # LOW 쌍, flip
        ]
        b = rr.calibration_buckets(self._series(rows))
        self.assertEqual(b["HIGH"]["rate"], 0.0)
        self.assertEqual(b["LOW"]["rate"], 1.0)
        self.assertIs(rr.calibration_inverted(b), False)

    def test_inverted_when_high_flips_more(self):
        rows = [
            sig(1, "2026-06-01", "positive", agreement="HIGH"),
            sig(1, "2026-06-02", "negative", agreement="HIGH"),   # HIGH flip
            sig(2, "2026-06-01", "positive", agreement="LOW"),
            sig(2, "2026-06-02", "positive", agreement="LOW"),    # LOW no flip
        ]
        b = rr.calibration_buckets(self._series(rows))
        self.assertIs(rr.calibration_inverted(b), True)

    def test_insufficient_returns_none(self):
        b = rr.calibration_buckets(self._series([sig(1, "2026-06-01", "positive", agreement="HIGH")]))
        self.assertIsNone(rr.calibration_inverted(b))


class CompositeIndexTest(unittest.TestCase):
    def test_perfect_inputs_max(self):
        idx = rr.composite_index(
            {"rate": 0.0}, {"ratio": 0.0}, {"ratio": 0.0}, 0, 30)
        self.assertEqual(idx, 100)

    def test_unmeasurable_flip_returns_none(self):
        self.assertIsNone(rr.composite_index({"rate": None}, {"ratio": None}, {"ratio": None}, None, 30))

    def test_degrades_with_flips(self):
        idx = rr.composite_index({"rate": 0.5}, {"ratio": 0.0}, {"ratio": 0.0}, 0, 30)
        self.assertEqual(idx, 78)  # 100*(0.45*0.5 + 0.25 + 0.15 + 0.15)


class CoverageShockTest(unittest.TestCase):
    def test_detects_spike_and_drop(self):
        cov = [
            {"day": "2026-06-01", "inserted": 10},
            {"day": "2026-06-02", "inserted": 9},
            {"day": "2026-06-03", "inserted": 11},
            {"day": "2026-06-04", "inserted": 80},   # 급증(>med*3)
            {"day": "2026-06-05", "inserted": 1},     # 급감(<med/3)
        ]
        shocks = rr.coverage_shock_days(cov)
        self.assertIn("2026-06-04", shocks)
        self.assertIn("2026-06-05", shocks)

    def test_too_few_days_no_shock(self):
        self.assertEqual(rr.coverage_shock_days([{"day": "x", "inserted": 5}]), [])


class FreshnessAndReportTest(unittest.TestCase):
    def test_freshness_days(self):
        signals = [sig(1, "2026-06-20", "positive"), sig(1, "2026-06-22", "positive")]
        self.assertEqual(rr.freshness_days(signals, date(2026, 6, 24)), 2)
        self.assertIsNone(rr.freshness_days([], date(2026, 6, 24)))

    def test_assemble_report_smoke_and_red_flags(self):
        # 단일소스 100% + 신선도 오래됨 → 빨강 플래그 2개.
        signals = [
            sig(1, "2026-06-01", "positive", breakdown={"HIRING": 60}),
            sig(1, "2026-06-02", "negative", breakdown={"HIRING": 40}),
        ]
        rep = rr.assemble_report(signals, [], date(2026, 6, 24), 30)
        self.assertEqual(rep["n_signals"], 2)
        self.assertEqual(rep["L2"]["flip"]["pairs"], 1)
        self.assertIsInstance(rep["composite_index"], int)
        joined = " ".join(rep["red_flags"])
        self.assertIn("수집 중단", joined)       # freshness 23일 > 7
        self.assertIn("단일소스", joined)         # single ratio 1.0 > 0.8


if __name__ == "__main__":
    unittest.main()
