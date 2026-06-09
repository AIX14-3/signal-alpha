import io
import json
import unittest
import zipfile
from urllib.error import URLError

from app.collectors.dart.disclosure import DartApiError, DartCollector, DartDisclosureClient


class FakeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def list_disclosures(self, **kwargs):
        self.calls.append(kwargs)
        return self.response

    async def fetch_document(self, receipt_no):
        self.calls.append({"receipt_no": receipt_no})
        return {"text": "Document body", "files": [{"name": "document.xml", "text_length": 13}]}


class FakePagedClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    async def list_disclosures(self, **kwargs):
        self.calls.append(kwargs)
        return self.responses[kwargs["page_no"]]

    async def fetch_document(self, receipt_no):
        self.calls.append({"receipt_no": receipt_no})
        return {"text": f"Document body {receipt_no}", "files": [{"name": "document.xml", "text_length": 27}]}


class FakeDocumentErrorClient(FakeClient):
    async def fetch_document(self, receipt_no):
        raise DartApiError(
            "rate limit",
            status="020",
            category="rate_limit",
            retryable=True,
        )


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
        self.assertEqual(evidence[0].content, "Document body")
        self.assertEqual(evidence[0].metadata["document_fetch_status"], "success")
        self.assertEqual(evidence[0].metadata["document_files"][0]["name"], "document.xml")

    async def test_collector_can_skip_document_fetch(self):
        client = FakeClient(
            {
                "status": "000",
                "list": [
                    {
                        "corp_code": "00126380",
                        "corp_name": "Samsung Electronics",
                        "stock_code": "005930",
                        "corp_cls": "Y",
                        "report_nm": "Quarterly report",
                        "rcept_no": "202606080001",
                        "flr_nm": "Samsung Electronics",
                        "rcept_dt": "20260608",
                        "rm": "",
                    }
                ],
            }
        )
        collector = DartCollector(
            api_key="test-key",
            corp_code_repository=FakeCorpCodeRepository({"corp_code": "00126380"}),
            client=client,
            fetch_documents=False,
        )

        evidence = await collector.collect("005930")

        self.assertEqual(evidence[0].content, "Quarterly report")
        self.assertNotIn("document_text", evidence[0].metadata)

    async def test_collector_marks_correction_disclosure_metadata(self):
        client = FakeClient(
            {
                "status": "000",
                "list": [
                    {
                        "corp_code": "00126380",
                        "corp_name": "Samsung Electronics",
                        "stock_code": "005930",
                        "corp_cls": "Y",
                        "report_nm": "[정정]Quarterly report",
                        "rcept_no": "202606080002",
                        "org_rcept_no": "202606080001",
                        "flr_nm": "Samsung Electronics",
                        "rcept_dt": "20260608",
                        "rm": "정정",
                    }
                ],
            }
        )
        collector = DartCollector(
            api_key="test-key",
            corp_code_repository=FakeCorpCodeRepository({"corp_code": "00126380"}),
            client=client,
            fetch_documents=False,
        )

        evidence = await collector.collect("005930")

        self.assertTrue(evidence[0].metadata["is_correction"])
        self.assertEqual(evidence[0].metadata["original_receipt_no"], "202606080001")
        self.assertEqual(evidence[0].metadata["disclosure_type"], "correction")
        self.assertEqual(evidence[0].metadata["correction_policy"], "separate_event")
        self.assertEqual(evidence[0].metadata["priority"], "immediate")

    async def test_collector_records_document_fetch_error_metadata(self):
        client = FakeDocumentErrorClient(
            {
                "status": "000",
                "list": [
                    {
                        "corp_code": "00126380",
                        "corp_name": "Samsung Electronics",
                        "stock_code": "005930",
                        "corp_cls": "Y",
                        "report_nm": "Quarterly report",
                        "rcept_no": "202606080001",
                        "flr_nm": "Samsung Electronics",
                        "rcept_dt": "20260608",
                        "rm": "",
                    }
                ],
            }
        )
        collector = DartCollector(
            api_key="test-key",
            corp_code_repository=FakeCorpCodeRepository({"corp_code": "00126380"}),
            client=client,
        )

        evidence = await collector.collect("005930")

        self.assertEqual(evidence[0].content, "Quarterly report")
        self.assertEqual(evidence[0].metadata["document_fetch_status"], "failed")
        self.assertEqual(evidence[0].metadata["document_error_category"], "rate_limit")
        self.assertTrue(evidence[0].metadata["document_error_retryable"])

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

    async def test_collector_fetches_all_pages_until_total_page(self):
        client = FakePagedClient(
            {
                1: {
                    "status": "000",
                    "page_no": "1",
                    "total_page": "2",
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
                },
                2: {
                    "status": "000",
                    "page_no": "2",
                    "total_page": "2",
                    "list": [
                        {
                            "corp_code": "00126380",
                            "corp_name": "삼성전자",
                            "stock_code": "005930",
                            "corp_cls": "Y",
                            "report_nm": "주요사항보고서",
                            "rcept_no": "202606080002",
                            "flr_nm": "삼성전자",
                            "rcept_dt": "20260608",
                            "rm": "",
                        }
                    ],
                },
            }
        )
        repository = FakeCorpCodeRepository({"corp_code": "00126380", "corp_name": "삼성전자"})
        collector = DartCollector(
            api_key="test-key",
            corp_code_repository=repository,
            client=client,
            start_date="20260601",
            end_date="20260608",
            page_size=1,
        )

        evidence = await collector.collect("005930")

        self.assertEqual([call["page_no"] for call in client.calls if "page_no" in call], [1, 2])
        self.assertEqual(len(evidence), 2)
        self.assertEqual(evidence[1].metadata["receipt_no"], "202606080002")


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

    def test_builds_document_url_with_receipt_number(self):
        client = DartDisclosureClient(
            api_key="test-key",
            base_url="https://opendart.example/api",
            timeout_seconds=10,
        )

        url = client.build_document_url("202606080001")

        self.assertEqual(
            url,
            "https://opendart.example/api/document.xml?crtfc_key=test-key&rcept_no=202606080001",
        )

    def test_parse_document_zip_extracts_text(self):
        body = _make_document_zip("<DOCUMENT><TITLE>Quarterly report</TITLE><BODY>Revenue grew.</BODY></DOCUMENT>")

        result = DartDisclosureClient.parse_document_zip(body)

        self.assertEqual(result["text"], "Quarterly report Revenue grew.")
        self.assertEqual(result["files"], [{"name": "document.xml", "text_length": 30}])

    def test_classifies_auth_status_as_non_retryable(self):
        error = DartApiError.from_status("010", "등록되지 않은 키입니다.")

        self.assertEqual(error.status, "010")
        self.assertEqual(error.category, "auth")
        self.assertFalse(error.retryable)

    def test_classifies_rate_limit_status_as_retryable(self):
        error = DartApiError.from_status("020", "요청 제한을 초과하였습니다.")

        self.assertEqual(error.status, "020")
        self.assertEqual(error.category, "rate_limit")
        self.assertTrue(error.retryable)

    def test_retries_retryable_status_before_returning_success(self):
        client = FakeRetryClient(
            [
                {"status": "020", "message": "요청 제한을 초과하였습니다."},
                {"status": "000", "list": []},
            ]
        )

        result = _run(client.list_disclosures(corp_code="00126380", bgn_de="20260601", end_de="20260608"))

        self.assertEqual(result["status"], "000")
        self.assertEqual(client.attempts, 2)

    def test_retries_network_errors_before_returning_success(self):
        client = FakeRetryClient(
            [
                URLError("temporary DNS failure"),
                {"status": "000", "list": []},
            ]
        )

        result = _run(client.list_disclosures(corp_code="00126380", bgn_de="20260601", end_de="20260608"))

        self.assertEqual(result["status"], "000")
        self.assertEqual(client.attempts, 2)


def _make_document_zip(xml_text):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("document.xml", xml_text)
    return buffer.getvalue()


def _run(coro):
    import asyncio

    return asyncio.run(coro)


class FakeRetryClient(DartDisclosureClient):
    def __init__(self, responses):
        super().__init__(
            api_key="test-key",
            base_url="https://opendart.example/api",
            timeout_seconds=10,
            max_retries=2,
            retry_backoff_seconds=0,
        )
        self.responses = list(responses)
        self.attempts = 0

    def _get_json(self, url):
        self.attempts += 1
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return json.loads(json.dumps(item))
