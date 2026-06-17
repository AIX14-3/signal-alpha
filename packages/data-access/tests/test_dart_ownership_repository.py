import unittest
from datetime import date

from signal_alpha_data_access.repositories.dart_ownership import DartOwnershipRepository


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


def _event(**overrides):
    base = {
        "corp_code": "00126380",
        "rcept_no": "20260315000123",
        "report_date": date(2026, 3, 15),
        "holder_name": "국민연금공단",
        "holder_type": "major",
        "shares": 70000000,
        "ratio": 7.5,
        "shares_delta": 1000000,
        "ratio_delta": 0.1,
        "report_reason": "장내매수",
        "stock_id": 1,
    }
    base.update(overrides)
    return base


class DartOwnershipRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_event_uses_natural_key_conflict_and_monotonic_guard(self):
        connection = FakeConnection()
        repository = DartOwnershipRepository(connection)

        await repository.upsert_event(
            corp_code="00126380",
            rcept_no="20260315000123",
            report_date=date(2026, 3, 15),
            holder_name="국민연금공단",
            holder_type="major",
            shares=70000000,
            stock_id=1,
        )

        sql = connection.calls[0][1]
        self.assertIn("(corp_code, rcept_no, holder_name, holder_type, line_seq)", sql)
        self.assertIn("dart_ownership_events.rcept_no <= EXCLUDED.rcept_no", sql)
        # 12개 바인드 파라미터(컬럼 수) 검증
        self.assertEqual(len(connection.calls[0][2]), 12)

    async def test_upsert_events_filters_missing_required_keys(self):
        connection = FakeConnection()
        repository = DartOwnershipRepository(connection)

        count = await repository.upsert_events(
            [
                _event(),
                _event(holder_name=""),  # holder_name 누락 → 제외
                _event(rcept_no=""),     # rcept_no 누락 → 제외
                _event(report_date=None),  # report_date 누락 → 제외
            ]
        )

        self.assertEqual(count, 1)
        self.assertEqual(connection.calls[0][0], "executemany")
        self.assertEqual(len(connection.calls[0][2]), 1)

    async def test_upsert_events_empty_returns_zero_without_query(self):
        connection = FakeConnection()
        repository = DartOwnershipRepository(connection)

        count = await repository.upsert_events([])

        self.assertEqual(count, 0)
        self.assertEqual(connection.calls, [])

    async def test_upsert_events_preserves_holder_type_and_deltas(self):
        connection = FakeConnection()
        repository = DartOwnershipRepository(connection)

        await repository.upsert_events(
            [_event(holder_type="executive", shares_delta=-5000, line_seq=2)]
        )

        row = connection.calls[0][2][0]
        # INSERT 컬럼 순서: stock_id(0), corp_code(1), rcept_no(2), line_seq(3),
        #                  report_date(4), holder_name(5), holder_type(6), shares(7),
        #                  ratio(8), shares_delta(9) ...
        self.assertEqual(row[3], 2)            # line_seq 보존
        self.assertEqual(row[6], "executive")
        self.assertEqual(row[9], -5000)

    async def test_line_seq_defaults_to_zero_when_missing(self):
        connection = FakeConnection()
        repository = DartOwnershipRepository(connection)

        event = _event()
        event.pop("line_seq", None)  # 수집기 미부여 시
        await repository.upsert_events([event])

        self.assertEqual(connection.calls[0][2][0][3], 0)

    async def test_get_latest_rcept_no_scopes_by_corp_and_holder_type(self):
        connection = FakeConnection()
        repository = DartOwnershipRepository(connection)

        await repository.get_latest_rcept_no(corp_code="00126380", holder_type="major")

        self.assertIn("MAX(rcept_no)", connection.calls[0][1])
        self.assertEqual(connection.calls[0][2], ("00126380", "major"))


if __name__ == "__main__":
    unittest.main()
