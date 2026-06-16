import unittest

from app.collectors.dart.disclosure import DartApiError
from app.collectors.dart.financials_api import (
    DartFinancialsCollector,
    _fiscal_period,
    _period_label,
    _to_krw,
)


def _ok_response(rows):
    return {"status": "000", "message": "정상", "list": rows}


_REVENUE_ROW = {
    "rcept_no": "20260315000123",
    "sj_div": "IS",
    "sj_nm": "손익계산서",
    "account_id": "ifrs-full_Revenue",
    "account_nm": "매출액",
    "thstrm_amount": "3,000,000,000,000",
    "frmtrm_amount": "2,500,000,000,000",
    "currency": "KRW",
}


class FakeFinancialsClient:
    def __init__(self, by_fs):
        self.by_fs = by_fs
        self.calls = []

    async def fetch_financials(self, *, corp_code, bsns_year, reprt_code, fs_div):
        self.calls.append({"fs_div": fs_div, "bsns_year": bsns_year, "reprt_code": reprt_code})
        return self.by_fs[fs_div]


class FakeCorpCodeRepository:
    def __init__(self, row):
        self.row = row

    async def get_corp_code_by_ticker(self, ticker):
        return self.row


class DartFinancialsCollectorTest(unittest.IsolatedAsyncioTestCase):
    async def _collect(self, by_fs):
        collector = DartFinancialsCollector(
            api_key="key",
            corp_code_repository=FakeCorpCodeRepository({"corp_code": "00126380"}),
            client=FakeFinancialsClient(by_fs),
        )
        return collector, await collector.collect(
            stock_code="005930", bsns_year=2025, reprt_code="11011"
        )

    async def test_parses_account_rows_into_facts(self):
        _, facts = await self._collect({"CFS": _ok_response([_REVENUE_ROW])})

        self.assertEqual(len(facts), 1)
        fact = facts[0]
        self.assertEqual(fact["corp_code"], "00126380")
        self.assertEqual(fact["rcept_no"], "20260315000123")
        self.assertEqual(fact["fs_div"], "CFS")
        self.assertEqual(fact["sj_div"], "IS")
        self.assertEqual(fact["account_id"], "ifrs-full_Revenue")
        self.assertEqual(fact["amount_krw"], 3000000000000)  # 당기만, 전기 무시
        self.assertEqual(fact["period_label"], "2025FY")
        self.assertEqual(fact["fiscal_period"], "annual")

    async def test_falls_back_to_ofs_when_cfs_has_no_data(self):
        collector, facts = await self._collect(
            {
                "CFS": {"status": "013", "message": "데이터 없음"},
                "OFS": _ok_response([_REVENUE_ROW]),
            }
        )

        self.assertEqual(len(facts), 1)
        self.assertEqual(facts[0]["fs_div"], "OFS")
        self.assertEqual([c["fs_div"] for c in collector._client.calls], ["CFS", "OFS"])

    async def test_blank_account_id_becomes_none(self):
        row = {**_REVENUE_ROW, "account_id": "", "account_nm": "기타"}
        _, facts = await self._collect({"CFS": _ok_response([row])})

        self.assertIsNone(facts[0]["account_id"])
        self.assertEqual(facts[0]["account_nm"], "기타")

    async def test_no_data_returns_empty(self):
        _, facts = await self._collect(
            {"CFS": {"status": "013"}, "OFS": {"status": "013"}}
        )
        self.assertEqual(facts, [])

    async def test_error_status_raises(self):
        with self.assertRaises(DartApiError):
            await self._collect({"CFS": {"status": "020", "message": "rate limit"}})


class FinancialsHelpersTest(unittest.TestCase):
    def test_to_krw_strips_commas_and_handles_blank(self):
        self.assertEqual(_to_krw("3,000,000,000,000"), 3000000000000)
        self.assertEqual(_to_krw("-1,234"), -1234)
        self.assertIsNone(_to_krw(""))
        self.assertIsNone(_to_krw("-"))
        self.assertIsNone(_to_krw(None))

    def test_period_label_and_fiscal_period(self):
        self.assertEqual(_period_label(2025, "11011"), "2025FY")
        self.assertEqual(_period_label(2025, "11014"), "2025Q3")
        self.assertEqual(_fiscal_period("11011"), "annual")
        self.assertEqual(_fiscal_period("11013"), "cumulative")


if __name__ == "__main__":
    unittest.main()
