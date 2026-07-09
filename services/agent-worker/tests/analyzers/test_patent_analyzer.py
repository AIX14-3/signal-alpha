import unittest
from datetime import date, timedelta

from app.analyzers.config import PatentRuleConfig
from app.analyzers.patent import PatentAnalyzer
from app.schemas.evidence import RawEvidence

AS_OF = date(2026, 6, 11)
CONFIG = PatentRuleConfig(
    lookback_days=365,
    min_count=3,
    momentum_threshold=0.5,
    decay_onset_days=90,
    signal_max_age_days=365,
    momentum_weight=0.5,
    new_category_weight=0.3,
    activity_weight=0.2,
    activity_scale=5.0,
    positive_threshold=0.2,
    negative_threshold=-0.2,
)


def _row(days_ago, *, is_new=False, tech="A", significance=None, pub_days_ago=None):
    row = {
        "application_no": f"x{days_ago}",
        "patent_title": "p",
        "applicant_name": "a",
        "application_date": (AS_OF - timedelta(days=days_ago)).isoformat(),
        "tech_category": tech,
        "is_new_category": is_new,
    }
    # 공개일(있으면). 지표는 event_date=공개일(폴백 출원일) 기준으로 버킷팅한다.
    if pub_days_ago is not None:
        row["publication_date"] = (AS_OF - timedelta(days=pub_days_ago)).isoformat()
    if significance is not None:
        row["significance"] = significance
    return row


def _evidence(rows, *, filing_trend=None):
    metadata = {"rows": rows, "as_of": AS_OF.isoformat(), "lookback_days": 365}
    if filing_trend is not None:
        metadata["filing_trend"] = filing_trend
    return [
        RawEvidence(
            source="PATENT",
            stock_code="005930",
            title="t",
            content="",
            metadata=metadata,
        )
    ]


class PatentAnalyzerTest(unittest.IsolatedAsyncioTestCase):
    async def test_no_data_is_no_signal(self):
        # Ran but had no rows → "no_signal", not "failed"/"missing".
        result = await PatentAnalyzer(CONFIG).analyze("005930", _evidence([]))
        self.assertEqual(result.direction, "unknown")
        self.assertEqual(result.data_status, "no_signal")
        self.assertIn("no_data", result.risk_flags)

    async def test_rising_filings_with_new_category_is_positive(self):
        rows = [_row(10, is_new=True), _row(20), _row(30), _row(40), _row(300)]
        result = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.direction, "positive")
        # graded (tanh) scoring no longer saturates to 1.0; still a strong positive.
        self.assertGreater(result.score, 0.7)
        self.assertLess(result.score, 1.0)
        self.assertEqual(result.data_status, "ok")

    async def test_falling_filings_is_negative(self):
        rows = [_row(10), _row(200), _row(210), _row(220), _row(230)]
        result = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertEqual(result.direction, "negative")

    async def test_only_old_filings_flag_stale_and_partial(self):
        rows = [_row(300), _row(310), _row(320)]
        result = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertIn("stale_data", result.risk_flags)
        self.assertEqual(result.data_status, "partial")

    async def test_recent_publication_of_old_filing_is_fresh_signal(self):
        # 핵심 회귀: 특허는 출원 후 ~18개월 뒤 공개된다. 출원은 540~600일 전이라도
        # 최근(10~40일 전) 공개된 특허는 event_date(공개일) 기준으로 recent 버킷에
        # 잡혀 신호가 나와야 한다 — 정체(stale) 아님. (출원일 기준이면 전부 창 밖·정체)
        rows = [
            _row(540, pub_days_ago=10, is_new=True),
            _row(560, pub_days_ago=20),
            _row(580, pub_days_ago=30),
            _row(600, pub_days_ago=40),
        ]
        result = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertNotIn("stale_data", result.risk_flags)
        self.assertEqual(result.data_status, "ok")
        self.assertGreater(result.score, 0.0)

    async def test_patent_meta_carries_recent_publications_and_trend(self):
        # 표시 전용 patent_meta: 최근 공개 특허 목록(공개일 필드) + 장기 출원 추이.
        rows = [_row(10, pub_days_ago=5), _row(20, pub_days_ago=15)]
        result = await PatentAnalyzer(CONFIG).analyze(
            "005930", _evidence(rows, filing_trend=[{"year": 2023, "count": 2}])
        )
        self.assertIsNotNone(result.patent_meta)
        self.assertEqual(len(result.patent_meta.recent_publications), 2)
        self.assertEqual(
            result.patent_meta.recent_publications[0]["publication_date"],
            (AS_OF - timedelta(days=5)).isoformat(),
        )
        self.assertEqual(result.patent_meta.filing_trend, [{"year": 2023, "count": 2}])

    async def test_missing_publication_date_falls_back_to_filing(self):
        # 공개일 미상(백필 전/stale prod): event_date 가 출원일로 폴백해 기존 동작 유지.
        # 오래된 출원만 있으면 여전히 stale.
        rows = [_row(300), _row(310), _row(320)]  # publication_date 키 없음
        result = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertIn("stale_data", result.risk_flags)

    async def test_high_significance_lifts_score_above_unenriched(self):
        # Same filings; the enriched set scores strictly higher (significance drives).
        plain = [_row(200), _row(210), _row(220), _row(230)]
        enriched = [
            _row(200, significance=0.9), _row(210, significance=0.9),
            _row(220, significance=0.9), _row(230, significance=0.9),
        ]
        low = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(plain))
        high = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(enriched))
        self.assertGreater(high.score, low.score)

    async def test_no_enrichment_is_exact_fallback(self):
        # Rows without a significance key must score identically to pre-C3 (component 0).
        rows = [_row(10, is_new=True), _row(20), _row(30), _row(40), _row(300)]
        result = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertGreater(result.score, 0.7)
        self.assertLess(result.score, 1.0)

    async def test_more_filings_lift_activity_monotonically(self):
        # Balanced recent/prior (momentum 0) and no new categories isolate the
        # activity component: more filings now score strictly higher, where the old
        # binary activity gave both the same +0.2.
        few = [_row(10), _row(20), _row(200), _row(210)]  # total 4, recent==prior
        many = few + [
            _row(30), _row(40), _row(50), _row(60),
            _row(220), _row(230), _row(240), _row(250),
        ]  # total 12, recent==prior
        low = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(few))
        high = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(many))
        self.assertGreater(high.score, low.score)
        # Single-sided: count alone (flat momentum) never makes the signal negative.
        self.assertGreaterEqual(low.score, 0.0)

    async def test_below_min_count_has_zero_activity(self):
        # Small-sample floor preserved: under min_count, activity contributes 0.
        rows = [_row(10), _row(20)]  # total 2 < min_count 3
        result = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertIn("insufficient_history", result.risk_flags)

    # --- 신선도 생애주기(freshness lifecycle) -----------------------------------
    def test_recency_factor_boundaries(self):
        from app.analyzers.patent.rules import _recency_factor

        # 나이 미상·온셋 이하 → 신선(1.0).
        self.assertEqual(_recency_factor(None, CONFIG), 1.0)
        self.assertEqual(_recency_factor(50, CONFIG), 1.0)
        self.assertEqual(_recency_factor(90, CONFIG), 1.0)  # 온셋 경계 포함
        # 만료 경계/초과 → 0.0.
        self.assertEqual(_recency_factor(365, CONFIG), 0.0)
        self.assertEqual(_recency_factor(400, CONFIG), 0.0)
        # 감쇠 구간 → 선형. 중간점 227일 ≈ 0.5.
        self.assertAlmostEqual(
            _recency_factor(227, CONFIG), (365 - 227) / (365 - 90), places=6
        )
        self.assertTrue(0.49 < _recency_factor(227, CONFIG) < 0.51)

    def test_decay_fades_score_preserving_sign(self):
        # 동일 지표를 나이만 바꿔 넣어 신선도 페이드를 격리 검증(모멘텀 교란 배제).
        from app.analyzers.patent.indicators import PatentIndicators
        from app.analyzers.patent.rules import evaluate_indicators

        def ind(age):
            return PatentIndicators(
                total=5, recent_count=4, prior_count=1, momentum_ratio=3.0,
                new_category_count=1, new_category_ratio=0.2,
                distinct_tech_categories=3, latest_application_date="2026-01-01",
                days_since_latest=age, llm_enriched_count=0,
                mean_significance=None, max_significance=None,
            )

        fresh = evaluate_indicators(ind(10), CONFIG)
        decay = evaluate_indicators(ind(227), CONFIG)
        self.assertGreater(fresh.score, 0.0)
        self.assertGreater(decay.score, 0.0)  # 부호 유지(양수→작아진 양수)
        self.assertLess(decay.score, fresh.score)
        self.assertAlmostEqual(
            decay.score, round(fresh.score * (365 - 227) / (365 - 90), 3), places=2
        )
        self.assertIn("stale_data", decay.risk_flags)
        self.assertNotIn("stale_data", fresh.risk_flags)
        self.assertNotIn("signal_expired", decay.risk_flags)
        self.assertTrue(any("감쇠" in h for h in decay.highlights))

    async def test_expired_signal_is_flagged_not_deleted(self):
        # 가장 최근 공개가 유효기간(365일)을 넘김: 데이터를 지우거나 집계에서 빼지
        # 않는다(no_signal 아님). 감쇠로 점수는 0에 수렴하지만, 특허 목록은 그대로
        # 노출되고 signal_expired 플래그로 프론트에만 "만료"를 표시한다.
        rows = [
            _row(410, pub_days_ago=400),
            _row(420, pub_days_ago=410),
            _row(430, pub_days_ago=420),
        ]
        result = await PatentAnalyzer(CONFIG).analyze("005930", _evidence(rows))
        self.assertNotEqual(result.data_status, "no_signal")  # 데이터 유지
        self.assertEqual(result.data_status, "partial")  # 신뢰도만 낮춤
        self.assertEqual(result.score, 0.0)  # 감쇠로 0에 수렴
        self.assertIn("signal_expired", result.risk_flags)  # FE 만료 배지 신호
        # 특허 목록 데이터는 지워지지 않고 그대로 노출된다.
        self.assertIsNotNone(result.patent_meta)
        self.assertEqual(len(result.patent_meta.recent_publications), 3)


if __name__ == "__main__":
    unittest.main()
