"""L1 데이터 무결성 게이트 — 수집된 패널·재무 parquet에 대한 검증.

데이터 파일이 아직 없으면 스킵된다 (수집 전 CI에서도 그린 유지).
실패는 곧 "팩터 작업 금지" — 설계 규율상 L1이 깨지면 데이터부터 고친다.
결측 지도는 stdout 리포트로 출력한다 (PR 본문에 첨부).
"""

from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
PANEL_PATH = DATA_DIR / "panel_kospi200.parquet"
FUND_PATH = DATA_DIR / "fundamentals_kospi200.parquet"

RETURN_JUMP_THRESHOLD = 0.40  # 수정주가 누락(분할 점프) 의심 경계
RETURN_JUMP_MAX_RATIO = 0.001  # 전체 행 대비 허용 비율 (상한가 30% 연속 등 정상 케이스 여유)


@unittest.skipUnless(PANEL_PATH.exists(), "panel parquet not collected yet")
class PanelIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.panel = pd.read_parquet(PANEL_PATH)
        cls.panel["trade_date"] = pd.to_datetime(cls.panel["trade_date"])

    def test_no_duplicate_date_ticker(self):
        duplicated = self.panel.duplicated(subset=["trade_date", "ticker"]).sum()
        self.assertEqual(duplicated, 0)

    def test_no_future_dates(self):
        self.assertLessEqual(self.panel["trade_date"].max().date(), date.today())

    def test_price_sanity(self):
        # 거래정지일은 OHLC가 0으로 오는 경우가 있어 close>0 행만 검사
        traded = self.panel[self.panel["close"] > 0]
        self.assertTrue((traded["high"] >= traded["low"]).all())
        self.assertTrue((traded["close"] <= traded["high"]).all())
        self.assertTrue((traded["close"] >= traded["low"]).all())

    def test_return_jumps_within_tolerance(self):
        """|일수익률| > 40% 행 보고 — pykrx는 수정주가라 다수 발생 시 버그."""
        panel = self.panel.sort_values(["ticker", "trade_date"])
        returns = panel.groupby("ticker")["close"].pct_change()
        jumps = panel[returns.abs() > RETURN_JUMP_THRESHOLD]
        if len(jumps):
            print("\n[결측 지도] 수익률 점프 의심 행 (수정주가 확인 필요):")
            print(jumps[["trade_date", "ticker", "name", "close"]].to_string(index=False))
        self.assertLessEqual(len(jumps) / max(len(panel), 1), RETURN_JUMP_MAX_RATIO)

    def test_report_halt_and_flow_missing_map(self):
        """거래정지 구간·수급 결측률 지도 — 게이트가 아닌 리포트 (항상 통과)."""
        halts = (
            self.panel[self.panel["volume"] == 0]
            .groupby("ticker")
            .size()
            .sort_values(ascending=False)
        )
        if len(halts):
            print("\n[결측 지도] 거래정지(volume=0) 일수 상위:")
            print(halts.head(10).to_string())
        flow_missing = (
            self.panel.groupby("ticker")["foreign_net"]
            .apply(lambda s: s.isna().mean())
            .sort_values(ascending=False)
        )
        print(f"\n[결측 지도] 수급 결측률 — 전체 {self.panel['foreign_net'].isna().mean():.1%}, "
              f"전결측 종목 수 {(flow_missing >= 0.999).sum()}")


@unittest.skipUnless(FUND_PATH.exists(), "fundamentals parquet not collected yet")
class FundamentalsIntegrityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fund = pd.read_parquet(FUND_PATH)

    def test_no_duplicate_period(self):
        duplicated = self.fund.duplicated(subset=["ticker", "fiscal_date"]).sum()
        self.assertEqual(duplicated, 0, f"중복 (ticker, fiscal_date) {duplicated}건")

    def test_available_date_is_point_in_time(self):
        """공시일은 회계기간 말일보다 뒤여야 한다 (미래정보 누수 차단의 핵심)."""
        dated = self.fund.dropna(subset=["available_date"])
        violations = dated[
            pd.to_datetime(dated["available_date"]) < pd.to_datetime(dated["fiscal_date"])
        ]
        self.assertEqual(len(violations), 0, violations.head().to_string())

    def test_report_fundamental_missing_map(self):
        print(f"\n[결측 지도] 재무 — available_date 결측 {self.fund['available_date'].isna().mean():.1%}, "
              f"영업이익 결측 {self.fund['operating_income'].isna().mean():.1%}, "
              f"자본총계 결측 {self.fund['total_equity'].isna().mean():.1%}")
        per_ticker = self.fund.groupby("ticker").size()
        print(f"종목당 기간 수 — 중앙값 {per_ticker.median():.0f}, 최소 {per_ticker.min()}")


if __name__ == "__main__":
    unittest.main()
