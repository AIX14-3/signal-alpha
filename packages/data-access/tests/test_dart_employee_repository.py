import unittest

from signal_alpha_data_access.repositories.dart_employee import DartEmployeeStatsRepository


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


def _stat(**overrides):
    base = {
        "corp_code": "00126380",
        "rcept_no": "20250311001085",
        "bsns_year": 2024,
        "reprt_code": "11011",
        "segment": "DX",
        "sex": "남",
        "headcount": 38291,
        "regular_count": 37953,
        "contract_count": 338,
        "avg_tenure_years": 16.9,
        "avg_salary_krw": None,
        "salary_total_krw": None,
        "stock_id": 1,
    }
    base.update(overrides)
    return base


class DartEmployeeStatsRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_stat_uses_coalesce_natural_key_and_monotonic_guard(self):
        connection = FakeConnection()
        repository = DartEmployeeStatsRepository(connection)

        await repository.upsert_stat(
            corp_code="00126380",
            rcept_no="20250311001085",
            bsns_year=2024,
            reprt_code="11011",
            segment="DX",
            sex="남",
            headcount=38291,
            stock_id=1,
        )

        sql = connection.calls[0][1]
        self.assertIn(
            "(corp_code, bsns_year, reprt_code, COALESCE(segment, ''), COALESCE(sex, ''), line_seq)",
            sql,
        )
        self.assertIn("dart_employee_stats.rcept_no <= EXCLUDED.rcept_no", sql)
        # 14개 바인드 파라미터(컬럼 수) 검증
        self.assertEqual(len(connection.calls[0][2]), 14)

    async def test_upsert_stats_filters_missing_required_keys(self):
        connection = FakeConnection()
        repository = DartEmployeeStatsRepository(connection)

        count = await repository.upsert_stats(
            [
                _stat(),
                _stat(rcept_no=""),       # rcept_no 누락 → 제외
                _stat(corp_code=None),    # corp_code 누락 → 제외
            ]
        )

        self.assertEqual(count, 1)
        self.assertEqual(connection.calls[0][0], "executemany")
        self.assertEqual(len(connection.calls[0][2]), 1)

    async def test_upsert_stats_empty_returns_zero_without_query(self):
        connection = FakeConnection()
        repository = DartEmployeeStatsRepository(connection)

        count = await repository.upsert_stats([])

        self.assertEqual(count, 0)
        self.assertEqual(connection.calls, [])

    async def test_upsert_stats_preserves_line_seq_segment_sex_and_salary(self):
        connection = FakeConnection()
        repository = DartEmployeeStatsRepository(connection)

        await repository.upsert_stats(
            [_stat(segment="성별합계", sex="여", line_seq=2, avg_salary_krw=106000000)]
        )

        row = connection.calls[0][2][0]
        # INSERT 컬럼 순서: stock_id(0), corp_code(1), rcept_no(2), line_seq(3),
        #                  bsns_year(4), reprt_code(5), segment(6), sex(7), headcount(8),
        #                  regular_count(9), contract_count(10), avg_tenure_years(11),
        #                  avg_salary_krw(12), salary_total_krw(13)
        self.assertEqual(row[3], 2)            # line_seq 보존
        self.assertEqual(row[6], "성별합계")
        self.assertEqual(row[7], "여")
        self.assertEqual(row[12], 106000000)

    async def test_line_seq_defaults_to_zero_when_missing(self):
        connection = FakeConnection()
        repository = DartEmployeeStatsRepository(connection)

        entry = _stat()
        entry.pop("line_seq", None)  # 수집기 미부여 시
        await repository.upsert_stats([entry])

        self.assertEqual(connection.calls[0][2][0][3], 0)

    async def test_get_latest_rcept_no_scopes_by_corp_year_reprt(self):
        connection = FakeConnection()
        repository = DartEmployeeStatsRepository(connection)

        await repository.get_latest_rcept_no(
            corp_code="00126380", bsns_year=2024, reprt_code="11011"
        )

        self.assertIn("MAX(rcept_no)", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], ("00126380", 2024, "11011"))


if __name__ == "__main__":
    unittest.main()
