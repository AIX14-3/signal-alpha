import unittest

from signal_alpha_data_access.repositories.sec import (
    SecFilingRepository,
    _normalize_cik,
    _to_date,
)


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"accession_no": args[6], "ticker": args[2]}

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return [{"accession_no": "0000000000-26-000001"}]

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return "2026-05-20"

    async def executemany(self, sql, rows):
        self.calls.append(("executemany", sql, rows))


class SecFilingRepositoryTest(unittest.IsolatedAsyncioTestCase):
    async def test_upsert_filing_normalizes_cik_and_passes_args(self):
        connection = FakeConnection()
        repository = SecFilingRepository(connection)

        await repository.upsert_filing(
            cik="1045810",
            ticker=" NVDA ",
            form="10-Q",
            filing_date="2026-05-20",
            accession_no="0001045810-26-000052",
        )

        op, _sql, args = connection.calls[0]
        self.assertEqual(op, "fetchrow")
        # cik(zero-pad), ticker(strip)
        self.assertEqual(args[1], "0001045810")
        self.assertEqual(args[2], "NVDA")
        self.assertEqual(args[6], "0001045810-26-000052")

    async def test_upsert_filings_skips_rows_missing_required_fields(self):
        connection = FakeConnection()
        repository = SecFilingRepository(connection)

        count = await repository.upsert_filings(
            [
                {
                    "cik": "1045810",
                    "ticker": "NVDA",
                    "form": "8-K",
                    "filing_date": "2026-05-20",
                    "accession_no": "0001045810-26-000051",
                },
                {"cik": "2488", "ticker": "AMD"},  # accession/form/date 누락 → 제외
            ]
        )

        self.assertEqual(count, 1)
        op, _sql, rows = connection.calls[0]
        self.assertEqual(op, "executemany")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][1], "0001045810")  # 정규화된 cik

    async def test_list_by_ticker_passes_limit(self):
        connection = FakeConnection()
        repository = SecFilingRepository(connection)

        await repository.list_by_ticker("NVDA", limit=5)

        op, _sql, args = connection.calls[0]
        self.assertEqual(op, "fetch")
        self.assertEqual(args, ("NVDA", 5))


class HelperTest(unittest.TestCase):
    def test_normalize_cik_zero_pads_digits(self):
        self.assertEqual(_normalize_cik("1045810"), "0001045810")
        self.assertEqual(_normalize_cik(2488), "0000002488")

    def test_normalize_cik_keeps_nondigit_as_is(self):
        self.assertEqual(_normalize_cik("CIK0001045810"), "CIK0001045810")

    def test_to_date_accepts_iso_and_compact(self):
        self.assertEqual(str(_to_date("2026-05-20")), "2026-05-20")
        self.assertEqual(str(_to_date("20260520")), "2026-05-20")


if __name__ == "__main__":
    unittest.main()
