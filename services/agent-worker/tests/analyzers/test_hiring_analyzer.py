"""
test_hiring_analyzer.py

HiringAnalyzer 핵심 로직 단위 테스트:
- _get_current_quarter(): 날짜 → 분기 변환
- _get_baseline_scale(): Phase A/B/C 3단계 fallback
- analyze_hiring_trend(): mock asyncpg 기반 DB 흐름 검증
- Spike 판정 임계값(150%) 경계값

asyncpg 의존성 없이 unittest.IsolatedAsyncioTestCase 사용.
"""
from __future__ import annotations

import unittest
from datetime import date, datetime
from unittest.mock import AsyncMock, MagicMock

from app.analyzers.hiring.hiring_analyzer import (
    HiringAnalyzer,
    HIRING_SPIKE_THRESHOLD,
    DEFAULT_BASELINE_SCALE,
    MIN_ROLLING_AVG_THRESHOLD,
)


def _reset_cache():
    HiringAnalyzer._baseline_cache = {}
    HiringAnalyzer._is_cache_loaded = False


# ── _normalize_query_date (asyncpg date 바인딩 회귀 가드) ─────────────────────

class TestNormalizeQueryDate(unittest.TestCase):
    """str/datetime → date 정규화. str 을 쿼리에 그대로 넘기면 asyncpg DataError
    ('str' object has no attribute 'toordinal')가 나던 회귀를 방지한다."""

    def setUp(self):
        self.a = HiringAnalyzer.__new__(HiringAnalyzer)

    def test_str_to_date(self):
        self.assertEqual(self.a._normalize_query_date("2026-06-17"), date(2026, 6, 17))

    def test_str_with_time_suffix(self):
        # "YYYY-MM-DD HH:MM:SS" 형태도 앞 10자리로 안전 처리.
        self.assertEqual(
            self.a._normalize_query_date("2026-06-17 09:30:00"), date(2026, 6, 17)
        )

    def test_datetime_to_date(self):
        self.assertEqual(
            self.a._normalize_query_date(datetime(2026, 6, 17, 9, 30)), date(2026, 6, 17)
        )

    def test_date_passthrough(self):
        d = date(2026, 6, 17)
        self.assertIs(self.a._normalize_query_date(d), d)


# ── _get_current_quarter ──────────────────────────────────────────────────────

class TestGetCurrentQuarter(unittest.TestCase):
    def setUp(self):
        self.a = HiringAnalyzer.__new__(HiringAnalyzer)

    def test_q1_boundaries(self):
        self.assertEqual(self.a._get_current_quarter("2024-01-01"), "Q1")
        self.assertEqual(self.a._get_current_quarter("2024-03-31"), "Q1")

    def test_q2_boundaries(self):
        self.assertEqual(self.a._get_current_quarter("2024-04-01"), "Q2")
        self.assertEqual(self.a._get_current_quarter("2024-06-30"), "Q2")

    def test_q3_boundaries(self):
        self.assertEqual(self.a._get_current_quarter("2024-07-01"), "Q3")
        self.assertEqual(self.a._get_current_quarter("2024-09-30"), "Q3")

    def test_q4_boundaries(self):
        self.assertEqual(self.a._get_current_quarter("2024-10-01"), "Q4")
        self.assertEqual(self.a._get_current_quarter("2024-12-31"), "Q4")


# ── _get_baseline_scale ───────────────────────────────────────────────────────

class TestGetBaselineScale(unittest.TestCase):
    def setUp(self):
        _reset_cache()
        self.a = HiringAnalyzer.__new__(HiringAnalyzer)

    def tearDown(self):
        _reset_cache()

    # _get_baseline_scale 은 이제 (scale: float, phase: str) 튜플을 반환한다.

    def test_phase_a_returns_rolling_avg(self):
        """Phase A: rolling_avg ≥ MIN_ROLLING_AVG_THRESHOLD → 이동평균 및 "A" 반환."""
        scale, phase = self.a._get_baseline_scale(stock_id=999, rolling_avg=MIN_ROLLING_AVG_THRESHOLD)
        self.assertEqual(scale, MIN_ROLLING_AVG_THRESHOLD)
        self.assertEqual(phase, "A")

    def test_phase_a_high_value(self):
        scale, phase = self.a._get_baseline_scale(stock_id=99, rolling_avg=25.0)
        self.assertEqual(scale, 25.0)
        self.assertEqual(phase, "A")

    def test_phase_b_uses_naver_volume(self):
        """Phase B: Cold Start → max(avg_search_volume/100, MIN_PHASE_B_EXPECTED) 반환."""
        from app.analyzers.hiring.hiring_analyzer import MIN_PHASE_B_EXPECTED
        HiringAnalyzer._baseline_cache[1] = {
            "avg_search_volume": 60.0, "Q1": 1.0, "Q2": 1.0, "Q3": 1.0, "Q4": 1.0
        }
        scale, phase = self.a._get_baseline_scale(stock_id=1, rolling_avg=0.0)
        # 60/100 = 0.6 > MIN_PHASE_B_EXPECTED(0.5) → 0.6 반환
        self.assertAlmostEqual(scale, max(60.0 / 100.0, MIN_PHASE_B_EXPECTED))
        self.assertEqual(phase, "B")

    def test_phase_b_low_volume_clamped(self):
        """Phase B: 검색량이 낮아 0.4 < MIN_PHASE_B_EXPECTED(0.5) → 0.5로 클램핑."""
        from app.analyzers.hiring.hiring_analyzer import MIN_PHASE_B_EXPECTED
        HiringAnalyzer._baseline_cache[1] = {
            "avg_search_volume": 40.0, "Q1": 1.0, "Q2": 1.0, "Q3": 1.0, "Q4": 1.0
        }
        scale, phase = self.a._get_baseline_scale(stock_id=1, rolling_avg=0.0)
        self.assertAlmostEqual(scale, MIN_PHASE_B_EXPECTED)
        self.assertEqual(phase, "B")

    def test_phase_b_no_cache_falls_to_c(self):
        """캐시 없음 → Phase C 기본값 1.0."""
        scale, phase = self.a._get_baseline_scale(stock_id=999, rolling_avg=0.0)
        self.assertEqual(scale, DEFAULT_BASELINE_SCALE)
        self.assertEqual(phase, "C")

    def test_phase_c_zero_avg_search_volume(self):
        """avg_search_volume = 0 → Phase C 기본값 1.0."""
        HiringAnalyzer._baseline_cache[1] = {
            "avg_search_volume": 0.0, "Q1": 1.0, "Q2": 1.0, "Q3": 1.0, "Q4": 1.0
        }
        scale, phase = self.a._get_baseline_scale(stock_id=1, rolling_avg=0.0)
        self.assertEqual(scale, DEFAULT_BASELINE_SCALE)
        self.assertEqual(phase, "C")

    def test_phase_a_b_boundary(self):
        """rolling_avg == MIN_ROLLING_AVG_THRESHOLD 는 Phase A (이동평균 사용)."""
        HiringAnalyzer._baseline_cache[1] = {
            "avg_search_volume": 80.0, "Q1": 1.0, "Q2": 1.0, "Q3": 1.0, "Q4": 1.0
        }
        scale, phase = self.a._get_baseline_scale(stock_id=1, rolling_avg=MIN_ROLLING_AVG_THRESHOLD)
        self.assertEqual(scale, MIN_ROLLING_AVG_THRESHOLD)
        self.assertEqual(phase, "A")


# ── Spike 판정 경계값 ─────────────────────────────────────────────────────────

class TestSpikeDetection(unittest.TestCase):
    """HIRING_SPIKE_THRESHOLD = 1.5 (150%) 경계값."""

    @staticmethod
    def _rs(today_count: int, base_scale: float, seasonal: float) -> float:
        expected = base_scale * seasonal
        if expected <= 0:
            expected = DEFAULT_BASELINE_SCALE
        return (today_count / expected) * 100

    def test_140pct_not_spike(self):
        rs = self._rs(14, 10.0, 1.0)
        self.assertAlmostEqual(rs, 140.0)
        self.assertFalse(rs >= HIRING_SPIKE_THRESHOLD * 100)

    def test_150pct_is_spike(self):
        rs = self._rs(15, 10.0, 1.0)
        self.assertAlmostEqual(rs, 150.0)
        self.assertTrue(rs >= HIRING_SPIKE_THRESHOLD * 100)

    def test_200pct_is_spike(self):
        rs = self._rs(20, 10.0, 1.0)
        self.assertAlmostEqual(rs, 200.0)
        self.assertTrue(rs >= HIRING_SPIKE_THRESHOLD * 100)

    def test_seasonal_factor_reduces_strength(self):
        """seasonal_factor 높을수록 상대 강도 낮아짐."""
        rs_base   = self._rs(15, 10.0, 1.0)
        rs_season = self._rs(15, 10.0, 1.2)
        self.assertLess(rs_season, rs_base)

    def test_zero_expected_falls_back_to_default(self):
        """expected ≤ 0 → DEFAULT_BASELINE_SCALE(1.0) 대체 → 5개 = 500%."""
        rs = self._rs(5, 0.0, 0.0)
        self.assertAlmostEqual(rs, 500.0)


# ── load_baselines ────────────────────────────────────────────────────────────

class TestLoadBaselines(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        _reset_cache()

    def tearDown(self):
        _reset_cache()

    async def test_loads_cache_from_db(self):
        row = {
            "stock_id": 1,
            "avg_search_volume": 42.5,
            "q1_factor": 1.1,
            "q2_factor": 0.9,
            "q3_factor": 0.8,
            "q4_factor": 1.2,
        }

        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[row])

        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        analyzer = HiringAnalyzer(pool)
        await analyzer.load_baselines()

        self.assertTrue(HiringAnalyzer._is_cache_loaded)
        self.assertIn(1, HiringAnalyzer._baseline_cache)
        cached = HiringAnalyzer._baseline_cache[1]
        self.assertAlmostEqual(cached["avg_search_volume"], 42.5)
        self.assertAlmostEqual(cached["Q1"], 1.1)
        self.assertAlmostEqual(cached["Q4"], 1.2)

    async def test_skips_reload_when_already_loaded(self):
        HiringAnalyzer._is_cache_loaded = True
        HiringAnalyzer._baseline_cache = {99: {"avg_search_volume": 10.0}}

        conn = AsyncMock()
        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

        analyzer = HiringAnalyzer(pool)
        await analyzer.load_baselines()

        conn.fetch.assert_not_called()
        self.assertIn(99, HiringAnalyzer._baseline_cache)


# ── analyze_hiring_trend ──────────────────────────────────────────────────────

class TestAnalyzeHiringTrend(unittest.IsolatedAsyncioTestCase):
    """mock asyncpg 연결로 전체 분석 흐름 검증."""

    def setUp(self):
        _reset_cache()
        HiringAnalyzer._is_cache_loaded = True
        HiringAnalyzer._baseline_cache = {
            1: {"avg_search_volume": 50.0, "Q1": 1.0, "Q2": 1.0, "Q3": 1.0, "Q4": 1.0}
        }

    def tearDown(self):
        _reset_cache()

    def _make_pool(self, scale_rows, today_rows):
        conn = AsyncMock()
        conn.fetch = AsyncMock(side_effect=[scale_rows, today_rows])
        conn.executemany = AsyncMock()  # 배치 업서트 전환 후 execute → executemany

        pool = MagicMock()
        pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
        pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        return pool, conn

    def _upsert_rows(self, conn) -> list[tuple]:
        """executemany 호출에서 실제 upsert_data 리스트를 추출하는 헬퍼."""
        sql, rows = conn.executemany.call_args[0]
        self.assertIn("INSERT INTO hiring_signals", sql)
        return rows

    async def test_inserts_signal_for_each_stock(self):
        scale_row = {"stock_id": 1, "rolling_avg": 5.0}
        today_row = {"stock_id": 1, "today_count": 8}
        pool, conn = self._make_pool([scale_row], [today_row])

        analyzer = HiringAnalyzer(pool)
        await analyzer.analyze_hiring_trend("2024-06-15")

        conn.executemany.assert_called_once()
        rows = self._upsert_rows(conn)
        stock_id, obs_date, job_count, *_ = rows[0]
        self.assertEqual(stock_id, 1)
        # observed_date 는 asyncpg $1::DATE 바인딩을 위해 date 객체여야 한다.
        # (str 로 들어가면 'str' object has no attribute 'toordinal' DataError — 회귀 가드)
        self.assertEqual(obs_date, date(2024, 6, 15))
        self.assertEqual(job_count, 8)

    async def test_phase_c_no_baseline_still_inserts(self):
        """baseline 없으면 Phase C(DEFAULT=1.0)로 분석 후 hiring_signals에 INSERT한다."""
        HiringAnalyzer._baseline_cache = {}
        today_row = {"stock_id": 1, "today_count": 5}
        pool, conn = self._make_pool([], [today_row])

        analyzer = HiringAnalyzer(pool)
        await analyzer.analyze_hiring_trend("2024-06-15")

        conn.executemany.assert_called_once()
        rows = self._upsert_rows(conn)
        stock_id, obs_date, job_count, baseline, *_ = rows[0]
        self.assertEqual(stock_id, 1)
        self.assertAlmostEqual(float(baseline), 1.0)

    async def test_spike_at_exactly_150pct(self):
        """15개 / rolling_avg 10.0 = 150% → is_spike=True."""
        scale_row = {"stock_id": 1, "rolling_avg": 10.0}
        today_row = {"stock_id": 1, "today_count": 15}
        pool, conn = self._make_pool([scale_row], [today_row])

        analyzer = HiringAnalyzer(pool)
        await analyzer.analyze_hiring_trend("2024-06-15")

        rows = self._upsert_rows(conn)
        # rows[0] = (stock_id, date, job_count, baseline, rs, is_spike, phase)
        _sid, _date, _count, _baseline, _rs, is_spike, _phase = rows[0]
        self.assertTrue(is_spike)

    async def test_no_spike_below_150pct(self):
        """14개 / rolling_avg 10.0 = 140% → is_spike=False."""
        scale_row = {"stock_id": 1, "rolling_avg": 10.0}
        today_row = {"stock_id": 1, "today_count": 14}
        pool, conn = self._make_pool([scale_row], [today_row])

        analyzer = HiringAnalyzer(pool)
        await analyzer.analyze_hiring_trend("2024-06-15")

        rows = self._upsert_rows(conn)
        _sid, _date, _count, _baseline, _rs, is_spike, _phase = rows[0]
        self.assertFalse(is_spike)

    async def test_cold_start_uses_naver_fallback(self):
        """rolling_avg 없는 기업은 네이버 검색량 기반 fallback 사용, MIN_PHASE_B_EXPECTED로 클램핑."""
        from app.analyzers.hiring.hiring_analyzer import MIN_PHASE_B_EXPECTED
        HiringAnalyzer._baseline_cache = {
            2: {"avg_search_volume": 40.0, "Q1": 1.0, "Q2": 1.0, "Q3": 1.0, "Q4": 1.0}
        }
        scale_rows = []  # Cold Start: 14일 이동평균 없음
        today_row  = {"stock_id": 2, "today_count": 3}
        pool, conn = self._make_pool(scale_rows, [today_row])

        analyzer = HiringAnalyzer(pool)
        await analyzer.analyze_hiring_trend("2024-06-15")

        conn.executemany.assert_called_once()
        rows = self._upsert_rows(conn)
        _, _, _, baseline, *_ = rows[0]
        # 40/100 = 0.4 < MIN_PHASE_B_EXPECTED(0.5) → 0.5로 클램핑됨
        self.assertAlmostEqual(baseline, MIN_PHASE_B_EXPECTED)


if __name__ == "__main__":
    unittest.main()
