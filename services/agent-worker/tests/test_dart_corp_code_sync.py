import io
import unittest
import warnings
import zipfile

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning,
)
from starlette.testclient import TestClient

from app.api.routes.dart import get_corp_code_sync_service_factory
from app.core.database import get_database_pool
from app.collectors.dart_corp_codes import parse_corp_code_zip
from app.main import app


def make_corp_code_zip() -> bytes:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<result>
  <list>
    <corp_code>00126380</corp_code>
    <corp_name>Samsung Electronics</corp_name>
    <stock_code>005930</stock_code>
    <modify_date>20260601</modify_date>
  </list>
  <list>
    <corp_code>00434003</corp_code>
    <corp_name>Unlisted Company</corp_name>
    <stock_code></stock_code>
    <modify_date>20260601</modify_date>
  </list>
</result>
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("CORPCODE.xml", xml)
    return buffer.getvalue()


class FakeConnection:
    pass


class FakeAcquire:
    async def __aenter__(self):
        return FakeConnection()

    async def __aexit__(self, exc_type, exc, traceback):
        return False


class FakePool:
    def acquire(self):
        return FakeAcquire()


class FakeCorpCodeSyncService:
    async def sync(self):
        return {"fetched_count": 2, "listed_count": 1, "upserted_count": 1}


class DartCorpCodeSyncTest(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_parse_corp_code_zip_returns_entries(self):
        entries = parse_corp_code_zip(make_corp_code_zip())

        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0].corp_code, "00126380")
        self.assertEqual(entries[0].stock_code, "005930")
        self.assertEqual(entries[1].stock_code, "")

    def test_sync_corp_codes_route_returns_counts(self):
        app.dependency_overrides[get_database_pool] = lambda: FakePool()
        app.dependency_overrides[get_corp_code_sync_service_factory] = (
            lambda: lambda connection, settings: FakeCorpCodeSyncService()
        )
        client = TestClient(app)

        response = client.post("/internal/dart/corp-codes/sync")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"fetched_count": 2, "listed_count": 1, "upserted_count": 1})


if __name__ == "__main__":
    unittest.main()
