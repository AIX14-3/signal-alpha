import unittest
from datetime import date, timedelta

from app.analyzers.config import HiringRuleConfig
from app.analyzers.hiring import HiringAnalyzer
from app.analyzers.hiring.indicators import compute_decayed_activity
from app.analyzers.hiring.rules import evaluate_decayed
from app.schemas.evidence import RawEvidence

AS_OF = date(2026, 6, 11)
CONFIG = HiringRuleConfig(
    lookback_days=90,
    min_observations=3,
    momentum_threshold=0.1,
    stale_days=45,
    momentum_weight=0.6,
    change_weight=0.4,
    positive_threshold=0.2,
    negative_threshold=-0.2,
)


def _row(days_ago, job_count, *, change_pct=None, seasonal_factor=1.0):
    return {
        "keyword": "k",
        "job_category": "dev",
        "job_count": job_count,
        "previous_job_count": None,
        "change_pct": change_pct,
        "observed_date": (AS_OF - timedelta(days=days_ago)).isoformat(),
        "seasonal_factor": seasonal_factor,
    }


def _evidence(rows, *, sector_demand=None):
    metadata = {"rows": rows, "as_of": AS_OF.isoformat(), "lookback_days": 90}
    if sector_demand is not None:
        metadata["sector_demand"] = sector_demand
    return [
        RawEvidence(
            source="HIRING",
            stock_code="005930",
            title="t",
            content="",
            metadata=metadata,
        )
    ]


class HiringAnalyzerTest(unittest.IsolatedAsyncioTestCase):
    """메타러너 폐기(2026-07) 후 결정론 verdict 복원 — 채용도 방향/점수를 낸다."""

    async def test_no_data_is_no_signal(self):
        result = await HiringAnalyzer(CONFIG).analyze("005930", _evidence([]))
        self.assertEqual(result.data_status, "no_signal")
        self.assertIn("no_data", result.risk_flags)

    async def test_rows_present_produce_verdict(self):
        rows = [
            _row(5, 150, change_pct=20), _row(10, 150, change_pct=20), _row(15, 150, change_pct=20),
            _row(55, 100), _row(60, 100), _row(65, 100), _row(70, 100), _row(75, 100),
        ]
        result = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        # 최근 평균 150 > 직전 100 → +50% 모멘텀 → 결정론 positive verdict, 점수 산입 대상.
        self.assertEqual(result.direction, "positive")
        self.assertGreater(result.score, 0.0)
        self.assertEqual(result.data_status, "ok")
        # 핵심 본질: 행이 있으면 서술 summary 가 생성된다 — 자연어 형식("…채용공고 N건…").
        self.assertIn("채용공고", result.summary)
        # 내부용어(피처 산출/메타러너)는 사용자 노출 summary 에 없다.
        self.assertNotIn("피처 산출", result.summary)
        self.assertNotIn("메타러너", result.summary)

    async def test_momentum_surfaced_as_percent(self):
        """추세(momentum)가 있으면 자연어 %로 노출된다(최근 150 > 직전 100 → +50%)."""
        rows = [
            _row(5, 150), _row(10, 150), _row(15, 150),
            _row(55, 100), _row(60, 100), _row(65, 100), _row(70, 100), _row(75, 100),
        ]
        result = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertIn("직전 대비", result.summary)
        self.assertIn("%", result.summary)

    async def test_tech_surfaced_but_job_title_noise_excluded(self):
        """tech_stack 은 summary·포커스 evidence 로 노출하되, job_title(스케줄·'모집' 등 노이즈)은 제외한다."""
        rows = [
            _row(5, 150), _row(10, 150), _row(15, 150),
            _row(55, 100), _row(60, 100), _row(65, 100), _row(70, 100), _row(75, 100),
        ]
        for r in rows[:3]:
            r["tech_stack"] = ["Python", "LLM"]
            r["job_title"] = "[주3일, 9시] 물류 모집"  # 스케줄·'모집' 노이즈
        fused = await HiringAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        # 소재 반영(본질): 기술은 노출 + 포커스 evidence 존재.
        self.assertIn("Python", fused.summary)
        self.assertTrue(any("포커스" in e.title for e in fused.evidence_items))
        # 노이즈 차단(회귀 핵심): job_title 의 '모집'·스케줄 토큰이 summary 에 안 들어간다.
        self.assertNotIn("모집", fused.summary)
        self.assertNotIn("주3일", fused.summary)
        self.assertNotIn("9시", fused.summary)
        # +50% 모멘텀 → 결정론 positive verdict.
        self.assertEqual(fused.direction, "positive")
        self.assertGreater(fused.score, 0.0)


def _plain(rows):
    """Copy rows without the descriptive fields (job_title/tech_stack)."""
    keys = ("keyword", "job_category", "job_count", "previous_job_count",
            "change_pct", "observed_date", "seasonal_factor")
    return [{k: r[k] for k in keys} for r in rows]


if __name__ == "__main__":
    unittest.main()


# ---------------------------------------------------------------------------
# 공고별 시간감쇠 스코어러(opt-in) — 지수 반감기 + 유효창(대기업/일반 차등)
# ---------------------------------------------------------------------------

DECAY_CONFIG = HiringRuleConfig(
    posting_decay_enabled=True,
    cycle_days_default=30,
    cycle_days_large=180,
    decay_half_life_ratio=0.5,
    activity_scale=3.0,
    activity_scale_large=3.0,  # 테스트 결정론화(캘리브레이션은 별도 테스트에서 검증)
    min_observations=3,
    positive_threshold=0.2,
    negative_threshold=-0.2,
    large_cap_tickers=frozenset({"005930"}),
)


def _posting(days_ago, *, closing=None, skills=None):
    return {
        "posting_date": (AS_OF - timedelta(days=days_ago)).isoformat(),
        "closing_date_parsed": closing,
        "ocr_skills": skills or [],
    }


def _decay_evidence(postings):
    return [
        RawEvidence(
            source="HIRING", stock_code="005930", title="t", content="",
            metadata={
                "rows": [{"observed_date": AS_OF.isoformat(), "job_count": 1}],  # _extract_rows 가드 통과용
                "postings": postings,
                "as_of": AS_OF.isoformat(),
            },
        )
    ]


class DecayedHiringIndicatorsTest(unittest.TestCase):
    def test_weight_halves_at_half_life(self):
        i0 = compute_decayed_activity([_posting(0)], as_of=AS_OF, window_days=30, half_life_days=15)
        i15 = compute_decayed_activity([_posting(15)], as_of=AS_OF, window_days=30, half_life_days=15)
        self.assertAlmostEqual(i0.decayed_activity, 1.0, places=3)
        self.assertAlmostEqual(i15.decayed_activity, 0.5, places=3)  # 반감기 → 절반

    def test_out_of_window_excluded_but_still_counted(self):
        i = compute_decayed_activity([_posting(40)], as_of=AS_OF, window_days=30, half_life_days=15)
        self.assertEqual(i.active_count, 0)      # 유효창 밖 → 점수 산입 제외
        self.assertEqual(i.total_postings, 1)    # 로드/DB 보존은 별개(카운트는 됨)
        self.assertEqual(i.decayed_activity, 0.0)

    def test_closed_posting_excluded(self):
        closed = _posting(2, closing=(AS_OF - timedelta(days=1)).isoformat())
        i = compute_decayed_activity([closed], as_of=AS_OF, window_days=30, half_life_days=15)
        self.assertEqual(i.active_count, 0)  # 이미 마감 → 제외

    def test_decays_over_calendar_time(self):
        posting = [_posting(2)]
        early = compute_decayed_activity(posting, as_of=AS_OF, window_days=30, half_life_days=15)
        late = compute_decayed_activity(
            posting, as_of=AS_OF + timedelta(days=20), window_days=30, half_life_days=15
        )
        self.assertGreater(early.decayed_activity, late.decayed_activity)  # 시간 지날수록 감소


class EvaluateDecayedTest(unittest.TestCase):
    def test_no_active_postings_is_unknown(self):
        i = compute_decayed_activity([_posting(40)], as_of=AS_OF, window_days=30, half_life_days=15)
        a = evaluate_decayed(i, DECAY_CONFIG)
        self.assertEqual(a.direction, "unknown")
        self.assertEqual(a.score, 0.0)
        self.assertIn("no_active_postings", a.risk_flags)

    def test_fresh_postings_positive(self):
        postings = [_posting(1), _posting(2), _posting(3), _posting(4)]
        i = compute_decayed_activity(postings, as_of=AS_OF, window_days=30, half_life_days=15)
        a = evaluate_decayed(i, DECAY_CONFIG)
        self.assertEqual(a.direction, "positive")
        self.assertGreater(a.score, 0.2)


class HiringAnalyzerDecayPathTest(unittest.IsolatedAsyncioTestCase):
    async def test_decay_path_scores_and_labels(self):
        postings = [_posting(1), _posting(3), _posting(5)]
        result = await HiringAnalyzer(DECAY_CONFIG).analyze("005930", _decay_evidence(postings))
        self.assertEqual(result.direction, "positive")
        self.assertEqual(result.data_status, "ok")
        self.assertIn("감쇠활동", result.summary)

    async def test_large_vs_default_window_differentiation(self):
        # 60일 된 공고: 대기업(005930·창180d)=활성, 일반(창30d)=만료(no_signal→FE 만료).
        postings = [_posting(60), _posting(61), _posting(62)]
        large = await HiringAnalyzer(DECAY_CONFIG).analyze("005930", _decay_evidence(postings))
        small = await HiringAnalyzer(DECAY_CONFIG).analyze("999999", _decay_evidence(postings))
        self.assertNotEqual(large.data_status, "no_signal")
        self.assertEqual(small.data_status, "no_signal")

    async def test_gate_off_uses_momentum_path(self):
        cfg_off = HiringRuleConfig(posting_decay_enabled=False, large_cap_tickers=frozenset({"005930"}))
        result = await HiringAnalyzer(cfg_off).analyze("005930", _decay_evidence([_posting(1)]))
        self.assertNotIn("감쇠활동", result.summary)  # 게이트 off → 현행 momentum 경로


class HiringDecayCalibrationAndExposureTest(unittest.IsolatedAsyncioTestCase):
    def test_larger_scale_lowers_score_for_same_activity(self):
        # 캘리브레이션: 대기업용 큰 scale → 같은 활동합이 더 낮은 점수(포화 늦춤).
        postings = [_posting(1), _posting(2), _posting(3)]
        i = compute_decayed_activity(postings, as_of=AS_OF, window_days=180, half_life_days=90)
        small = evaluate_decayed(i, DECAY_CONFIG, activity_scale=3.0).score
        large = evaluate_decayed(i, DECAY_CONFIG, activity_scale=15.0).score
        self.assertGreater(small, large)

    def test_active_postings_exposed_for_fe_age(self):
        # FE age 재료: active_postings 는 게시일(절대날짜) 최신순. age 숫자 저장 아님.
        i = compute_decayed_activity(
            [_posting(5, closing="2026-07-31"), _posting(1)], as_of=AS_OF, window_days=30, half_life_days=15
        )
        self.assertEqual(len(i.active_postings), 2)
        dates = [d for d, _c in i.active_postings]
        self.assertEqual(dates, sorted(dates, reverse=True))  # 최신순
        self.assertTrue(all(len(d) == 10 for d in dates))     # ISO 날짜(박제 age 아님)

    async def test_analyzer_emits_per_posting_evidence_with_posting_date(self):
        postings = [_posting(1), _posting(3), _posting(5)]
        result = await HiringAnalyzer(DECAY_CONFIG).analyze("005930", _decay_evidence(postings))
        posting_ev = [e for e in result.evidence_items if e.title == "채용공고"]
        self.assertEqual(len(posting_ev), 3)  # 공고별 근거 노출
        # 각 근거는 게시일(절대날짜)을 published_at 으로 실어 FE 가 "N일 전"을 계산할 수 있다.
        self.assertTrue(all(e.published_at for e in posting_ev))


class HiringDeclineDetectionTest(unittest.TestCase):
    """(3) level + 급감 감지: 최근절반 활동이 직전절반 대비 급감하면 negative."""

    def test_decline_flips_to_negative(self):
        # 직전절반(age 16~24)에 4건, 최근절반(age≤15)에 0건 → 채용 급감 → negative.
        postings = [_posting(18), _posting(20), _posting(22), _posting(24)]
        i = compute_decayed_activity(postings, as_of=AS_OF, window_days=30, half_life_days=15)
        self.assertGreater(i.prior_half_decayed, 0)
        self.assertEqual(i.recent_half_decayed, 0.0)
        a = evaluate_decayed(i, DECAY_CONFIG, activity_scale=3.0)
        self.assertEqual(a.direction, "negative")
        self.assertLess(a.score, 0)
        self.assertIn("hiring_decline", a.risk_flags)

    def test_steady_activity_stays_positive(self):
        # 최근절반·직전절반 고르게 → 급감 아님 → level 기반 positive 유지.
        postings = [_posting(2), _posting(4), _posting(6), _posting(18), _posting(20), _posting(22)]
        i = compute_decayed_activity(postings, as_of=AS_OF, window_days=30, half_life_days=15)
        a = evaluate_decayed(i, DECAY_CONFIG, activity_scale=3.0)
        self.assertNotEqual(a.direction, "negative")
        self.assertGreater(a.score, 0)
        self.assertNotIn("hiring_decline", a.risk_flags)


# long_term_avg(옵션3): 급감 기준을 유효창 반분할이 아닌 회사 장기 채용률 평균 대비로 판정.
LONG_TERM_CONFIG = HiringRuleConfig(
    posting_decay_enabled=True,
    cycle_days_default=30,
    cycle_days_large=180,
    decay_half_life_ratio=0.5,
    activity_scale=3.0,
    activity_scale_large=3.0,
    min_observations=1,
    positive_threshold=0.2,
    negative_threshold=-0.2,
    baseline_mode="long_term_avg",
    long_term_window_days=365,
    min_baseline_buckets=2,
    large_cap_tickers=frozenset({"005930"}),
)


class HiringLongTermBaselineTest(unittest.TestCase):
    """옵션3: 회사 과거 활성공고율(트레일링 장기창 버킷 평균) 대비 현재창 급감 감지."""

    def test_baseline_computed_only_when_window_given(self):
        # 장기창 미전달(기본) → baseline 필드 0(=경로 비활성, prior_window 와 무관).
        postings = [_posting(40), _posting(45), _posting(70), _posting(75)]
        i = compute_decayed_activity(postings, as_of=AS_OF, window_days=30, half_life_days=15)
        self.assertEqual(i.long_term_avg_decayed, 0.0)
        self.assertEqual(i.long_term_baseline_buckets, 0)

    def test_baseline_averages_nonempty_past_buckets(self):
        # 현재창(age<30)엔 공고 0, 과거 버킷 2개(30~59, 60~89)에 각 공고 → baseline>0·버킷2.
        postings = [_posting(40), _posting(45), _posting(70), _posting(75)]
        i = compute_decayed_activity(
            postings, as_of=AS_OF, window_days=30, half_life_days=15, long_term_window_days=365
        )
        self.assertEqual(i.long_term_baseline_buckets, 2)
        self.assertGreater(i.long_term_avg_decayed, 0.0)
        self.assertEqual(i.decayed_activity, 0.0)  # 현재창엔 활성 공고 없음

    def test_decline_vs_long_term_avg_flips_negative(self):
        # 과거 2버킷엔 채용활동, 현재창엔 거의 없음(1건·오래됨) → 장기평균 대비 급감 → negative.
        postings = [
            _posting(40), _posting(45), _posting(48),   # 버킷1 (30~59)
            _posting(70), _posting(75), _posting(78),   # 버킷2 (60~89)
            _posting(28),                               # 현재창 끝자락 1건(약한 활동)
        ]
        i = compute_decayed_activity(
            postings, as_of=AS_OF, window_days=30, half_life_days=15, long_term_window_days=365
        )
        a = evaluate_decayed(i, LONG_TERM_CONFIG, activity_scale=3.0)
        self.assertGreaterEqual(i.long_term_baseline_buckets, 2)
        self.assertLess(i.decayed_activity, i.long_term_avg_decayed * 0.5)
        self.assertEqual(a.direction, "negative")
        self.assertIn("hiring_decline", a.risk_flags)
        self.assertIn("장기평균", a.highlights[-1])

    def test_current_matches_long_term_stays_positive(self):
        # 현재창 활동이 장기평균과 비슷 → 급감 아님 → positive 유지.
        postings = [
            _posting(2), _posting(5), _posting(8),      # 현재창 (활발)
            _posting(40), _posting(45),                 # 버킷1
            _posting(70), _posting(75),                 # 버킷2
        ]
        i = compute_decayed_activity(
            postings, as_of=AS_OF, window_days=30, half_life_days=15, long_term_window_days=365
        )
        a = evaluate_decayed(i, LONG_TERM_CONFIG, activity_scale=3.0)
        self.assertNotEqual(a.direction, "negative")
        self.assertNotIn("hiring_decline", a.risk_flags)
        self.assertGreater(a.score, 0)

    def test_insufficient_baseline_suppresses_decline(self):
        # 과거 버킷 1개뿐(min_baseline_buckets=2 미달) → 이력 부족으로 급감 판정 보류.
        # 현재창 공고 없고 과거 1버킷만 있어도 negative 로 뒤집지 않는다(유령 negative 방지).
        postings = [_posting(40), _posting(45), _posting(48)]  # 버킷1(30~59)만
        i = compute_decayed_activity(
            postings, as_of=AS_OF, window_days=30, half_life_days=15, long_term_window_days=365
        )
        a = evaluate_decayed(i, LONG_TERM_CONFIG, activity_scale=3.0)
        self.assertEqual(i.long_term_baseline_buckets, 1)
        self.assertNotIn("hiring_decline", a.risk_flags)

    def test_prior_window_default_unaffected(self):
        # 회귀 가드: 기본 baseline_mode(prior_window)는 장기평균 필드를 무시한다.
        postings = [_posting(18), _posting(20), _posting(22), _posting(24)]  # 직전절반만
        i = compute_decayed_activity(
            postings, as_of=AS_OF, window_days=30, half_life_days=15, long_term_window_days=365
        )
        a = evaluate_decayed(i, DECAY_CONFIG, activity_scale=3.0)  # prior_window
        self.assertEqual(a.direction, "negative")  # 반분할 급감 그대로
        self.assertIn("hiring_decline", a.risk_flags)
        self.assertIn("최근절반", a.highlights[-1])  # prior_window 문구
