import unittest

from app.collectors.report.storage import GcsReportStorageClient, get_report_storage_client


class FakeBlob:
    def __init__(self) -> None:
        self.exists_value = False
        self.uploaded_bytes = None
        self.content_type = None

    def exists(self) -> bool:
        return self.exists_value

    def upload_from_string(self, data: bytes, content_type: str) -> None:
        self.uploaded_bytes = data
        self.content_type = content_type
        self.exists_value = True

    def download_as_bytes(self) -> bytes:
        return self.uploaded_bytes or b""


class FakeBucket:
    def __init__(self) -> None:
        self.blobs = {}

    def blob(self, key: str) -> FakeBlob:
        if key not in self.blobs:
            self.blobs[key] = FakeBlob()
        return self.blobs[key]


class FakeGcsClient:
    def __init__(self) -> None:
        self.buckets = {}

    def bucket(self, name: str) -> FakeBucket:
        if name not in self.buckets:
            self.buckets[name] = FakeBucket()
        return self.buckets[name]


class Settings:
    report_storage_backend = "gcs"
    gcs_report_bucket = "signal-alpha-report-fixtures"


class ReportStorageTest(unittest.TestCase):
    def test_gcs_report_storage_uses_bucket_blob_operations(self):
        client = FakeGcsClient()
        storage = GcsReportStorageClient(
            bucket_name="signal-alpha-report-fixtures",
            client=client,
        )

        self.assertFalse(storage.exists("reports/005930/a.pdf"))

        storage.upload_pdf(b"%PDF-fake", "reports/005930/a.pdf")

        blob = client.bucket("signal-alpha-report-fixtures").blob("reports/005930/a.pdf")
        self.assertTrue(storage.exists("reports/005930/a.pdf"))
        self.assertEqual(blob.uploaded_bytes, b"%PDF-fake")
        self.assertEqual(blob.content_type, "application/pdf")
        self.assertEqual(storage.download_pdf("reports/005930/a.pdf"), b"%PDF-fake")

    def test_factory_builds_gcs_storage_from_settings(self):
        client = FakeGcsClient()

        storage = get_report_storage_client(Settings(), gcs_client=client)

        self.assertIsInstance(storage, GcsReportStorageClient)
        storage.upload_pdf(b"%PDF-fake", "reports/005930/a.pdf")
        self.assertTrue(client.bucket(Settings.gcs_report_bucket).blob("reports/005930/a.pdf").exists())


if __name__ == "__main__":
    unittest.main()

