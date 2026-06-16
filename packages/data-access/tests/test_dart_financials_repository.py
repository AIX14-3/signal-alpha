import unittest

from signal_alpha_data_access.repositories.dart_financials import DartFinancialFactsRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 1}

    async def executemany(self, sql, args):
        self.calls.append(("executemany", sql, args))

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return []

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return None


def _fact(**overrides):
    base = {
        "corp_code": "00126380",
        "rcept_no": "20260315000123",
        "bsns_year": 2025,
        "reprt_code": "11011",
        "fs_div": "CFS",
        "sj_div": "IS",
        "account_id": "ifrs-full_Revenue",
        "account_nm": "매출액",
        "amount_krw": 3000000000000,
        "amount_raw": "3,000,000,000,000",
        "currency": "KRW",
        "period_label": "2025FY",
        "fiscal_period": "annual",
        "stock_id": 1,
    }
    base.update(overrides)
    return base


class DartFinancialFactsRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_fact_uses_coalesce_conflict_and_monotonic_guard(self):
        connection = FakeConnection()
        repository = DartFinancialFactsRepository(connection)

        await repository.upsert_fact(
            corp_code="00126380",
            rcept_no="20260315000123",
            bsns_year=2025,
            reprt_code="11011",
            fs_div="CFS",
            sj_div="IS",
            account_nm="매출액",
            account_id="ifrs-full_Revenue",
            amount_krw=3000000000000,
            stock_id=1,
        )

        sql = connection.calls[0][1]
        self.assertIn("COALESCE(account_id, account_nm)", sql)
        self.assertIn("dart_financial_facts.rcept_no <= EXCLUDED.rcept_no", sql)
        # 14개 바인드 파라미터(컬럼 수) 검증
        self.assertEqual(len(connection.calls[0][2]), 14)

    async def test_upsert_facts_filters_missing_required_keys(self):
        connection = FakeConnection()
        repository = DartFinancialFactsRepository(connection)

        count = await repository.upsert_facts(
            [
                _fact(),
                _fact(account_nm="", account_id=None),  # account_nm 누락 → 제외
                _fact(rcept_no=""),                      # rcept_no 누락 → 제외
            ]
        )

        self.assertEqual(count, 1)
        self.assertEqual(connection.calls[0][0], "executemany")
        self.assertEqual(len(connection.calls[0][2]), 1)

    async def test_upsert_facts_empty_returns_zero_without_query(self):
        connection = FakeConnection()
        repository = DartFinancialFactsRepository(connection)

        count = await repository.upsert_facts([])

        self.assertEqual(count, 0)
        self.assertEqual(connection.calls, [])

    async def test_account_id_falls_back_to_none_when_blank(self):
        connection = FakeConnection()
        repository = DartFinancialFactsRepository(connection)

        await repository.upsert_facts([_fact(account_id="", account_nm="기타비표준")])

        row = connection.calls[0][2][0]
        # 인덱스 7 = account_id (INSERT 컬럼 순서) → 빈 값은 None
        self.assertIsNone(row[7])
        self.assertEqual(row[8], "기타비표준")

    async def test_get_latest_rcept_no_scopes_by_period(self):
        connection = FakeConnection()
        repository = DartFinancialFactsRepository(connection)

        await repository.get_latest_rcept_no(
            corp_code="00126380", bsns_year=2025, reprt_code="11011", fs_div="CFS"
        )

        self.assertIn("MAX(rcept_no)", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], ("00126380", 2025, "11011", "CFS"))


if __name__ == "__main__":
    unittest.main()
