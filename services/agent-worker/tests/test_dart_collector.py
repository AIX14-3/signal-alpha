import unittest

from app.collectors.dart import DartApiError, DartCollector, DartDisclosureClient


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def list_disclosures(self, **kwargs):
        self.calls.append(kwargs)
        return self.response


class FakeCorpCodeRepository:
    def __init__(self, row):
        self.row = row
        self.calls = []

    async def get_corp_code_by_ticker(self, ticker):
        self.calls.append(ticker)
        return self.row


class DartCollectorTest(unittest.IsolatedAsyncioTestCase):
    async def test_collector_maps_disclosure_list_to_raw_evidence(self):
        client = FakeClient(
            {
                "status": "000",
                "message": "정상",
                "list": [
                    {
                        "corp_code": "00126380",
                        "corp_name": "삼성전자",
                        "stock_code": "005930",
                        "corp_cls": "Y",
                        "report_nm": "분기보고서",
                        "rcept_no": "202606080001",
                        "flr_nm": "삼성전자",
                        "rcept_dt": "20260608",
                        "rm": "",
                    }
                ],
            }
        )
        repository = FakeCorpCodeRepository({"corp_code": "00126380", "corp_name": "삼성전자"})
        collector = DartCollector(
            api_key="test-key",
            corp_code_repository=repository,
            client=client,
            start_date="20260601",
            end_date="20260608",
            page_size=50,
        )

        evidence = await collector.collect(" 005930 ")

        self.assertEqual(repository.calls, ["005930"])
        self.assertEqual(client.calls[0]["corp_code"], "00126380")
        self.assertEqual(client.calls[0]["bgn_de"], "20260601")
        self.assertEqual(evidence[0].source, "DART")
        self.assertEqual(evidence[0].stock_code, "005930")
        self.assertEqual(evidence[0].title, "분기보고서")
        self.assertEqual(evidence[0].published_at, "2026-06-08")
        self.assertEqual(evidence[0].metadata["receipt_no"], "202606080001")
        self.assertEqual(evidence[0].metadata["external_id"], "202606080001")
        self.assertEqual(evidence[0].metadata["corp_code"], "00126380")
        self.assertEqual(evidence[0].metadata["source_name"], "OpenDART")

    async def test_collector_returns_empty_list_when_dart_has_no_data(self):
        client = FakeClient({"status": "013", "message": "조회된 데이타가 없습니다."})
        repository = FakeCorpCodeRepository({"corp_code": "00126380", "corp_name": "삼성전자"})
        collector = DartCollector(
            api_key="test-key",
            corp_code_repository=repository,
            client=client,
            start_date="20260601",
            end_date="20260608",
        )

        evidence = await collector.collect("005930")

        self.assertEqual(evidence, [])

    async def test_collector_raises_when_corp_code_is_missing(self):
        collector = DartCollector(
            api_key="test-key",
            corp_code_repository=FakeCorpCodeRepository(None),
            client=FakeClient({"status": "000", "list": []}),
        )

        with self.assertRaises(DartApiError):
            await collector.collect("005930")


class DartDisclosureClientTest(unittest.TestCase):
    def test_builds_list_url_with_expected_parameters(self):
        client = DartDisclosureClient(
            api_key="test-key",
            base_url="https://opendart.example/api",
            timeout_seconds=10,
        )

        url = client.build_list_url(
            corp_code="00126380",
            bgn_de="20260601",
            end_de="20260608",
            page_no=1,
            page_count=50,
        )

        self.assertTrue(url.startswith("https://opendart.example/api/list.json?"))
        self.assertIn("crtfc_key=test-key", url)
        self.assertIn("corp_code=00126380", url)
        self.assertIn("bgn_de=20260601", url)
        self.assertIn("page_count=50", url)
