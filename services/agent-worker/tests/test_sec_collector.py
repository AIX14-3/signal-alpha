import unittest

import httpx

from app.collectors.sec import (
    SecEdgarClient,
    build_document_url,
    parse_recent_filings,
    parse_ticker_map,
)
from app.core.config import Settings

_TICKER_MAP = {
    "0": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "1": {"cik_str": 2488, "ticker": "AMD", "title": "ADVANCED MICRO DEVICES INC"},
}

_SUBMISSIONS = {
    "name": "NVIDIA CORP",
    "filings": {
        "recent": {
            "form": ["10-Q", "8-K", "4", "10-K"],
            "filingDate": ["2026-05-20", "2026-05-20", "2026-05-10", "2026-02-25"],
            "accessionNumber": [
                "0001045810-26-000052",
                "0001045810-26-000051",
                "0001045810-26-000040",
                "0001045810-26-000021",
            ],
            "primaryDocument": ["nvda-q.htm", "nvda-8k.htm", "form4.xml", "nvda-k.htm"],
            "primaryDocDescription": ["10-Q", "8-K", "FORM 4", "10-K"],
        }
    },
}


def _fast_settings() -> Settings:
    settings = Settings()
    settings.sec_min_request_interval_sec = 0.0  # 테스트 throttle 제거
    settings.sec_max_retries = 0
    return settings


def _mock_client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("company_tickers.json"):
            return httpx.Response(200, json=_TICKER_MAP)
        if "/submissions/CIK" in path:
            return httpx.Response(200, json=_SUBMISSIONS)
        return httpx.Response(404, text="not found")

    return httpx.Client(transport=httpx.MockTransport(handler))


class PureParseTest(unittest.TestCase):
    def test_parse_ticker_map_uppercases_and_ints(self):
        mapping = parse_ticker_map(_TICKER_MAP)
        self.assertEqual(mapping["NVDA"], 1045810)
        self.assertEqual(mapping["AMD"], 2488)

    def test_build_document_url_drops_dashes_and_pads_path(self):
        url = build_document_url(1045810, "0001045810-26-000052", "nvda-q.htm")
        self.assertEqual(
            url,
            "https://www.sec.gov/Archives/edgar/data/1045810/000104581026000052/nvda-q.htm",
        )

    def test_parse_recent_filings_filters_forms_and_limit(self):
        filings = parse_recent_filings(
            _SUBMISSIONS, ticker="nvda", cik_int=1045810, forms={"8-K", "10-K"}, limit=10
        )
        self.assertEqual([f.form for f in filings], ["8-K", "10-K"])
        self.assertEqual(filings[0].ticker, "NVDA")
        self.assertEqual(filings[0].cik, "0001045810")
        self.assertTrue(filings[0].document_url.endswith("nvda-8k.htm"))

    def test_parse_recent_filings_no_filter_respects_limit(self):
        filings = parse_recent_filings(
            _SUBMISSIONS, ticker="NVDA", cik_int=1045810, forms=None, limit=2
        )
        self.assertEqual(len(filings), 2)

    def test_as_row_has_repository_fields(self):
        filings = parse_recent_filings(
            _SUBMISSIONS, ticker="NVDA", cik_int=1045810, forms=None, limit=1
        )
        row = filings[0].as_row()
        for key in ("cik", "ticker", "form", "filing_date", "accession_no", "document_url"):
            self.assertIn(key, row)


class SecEdgarClientTest(unittest.TestCase):
    def test_fetch_filings_resolves_ticker_and_returns_filings(self):
        with SecEdgarClient(_fast_settings(), client=_mock_client()) as client:
            filings = client.fetch_filings("NVDA", forms={"10-Q"}, limit=5)
        self.assertEqual(len(filings), 1)
        self.assertEqual(filings[0].form, "10-Q")
        self.assertEqual(filings[0].company_name, "NVIDIA CORP")

    def test_fetch_filings_unknown_ticker_returns_empty(self):
        with SecEdgarClient(_fast_settings(), client=_mock_client()) as client:
            self.assertEqual(client.fetch_filings("OPENAI"), [])

    def test_load_ticker_to_cik(self):
        with SecEdgarClient(_fast_settings(), client=_mock_client()) as client:
            mapping = client.load_ticker_to_cik()
        self.assertEqual(mapping["AMD"], 2488)


if __name__ == "__main__":
    unittest.main()
