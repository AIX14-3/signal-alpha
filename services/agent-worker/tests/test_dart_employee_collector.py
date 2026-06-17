import unittest

from app.collectors.dart.disclosure import DartApiError
from app.collectors.dart.employee_api import (
    DartEmployeeClient,
    DartEmployeeCollector,
    _to_int,
    _to_ratio,
)


def _ok_response(rows):
    return {"status": "000", "message": "정상", "list": rows}


# 라이브 검증(삼성 00126380, 2024)에서 확인된 사업부문별 행(급여 필드는 '-').
_SEGMENT_ROW = {
    "rcept_no": "20250311001085",
    "sexdstn": "남",
    "fo_bbm": "DX",
    "rgllbr_co": "37,953",
    "cnttk_co": "338",
    "sm": "38,291",
    "avrg_cnwk_sdytrn": "16.9",
    "fyer_salary_totamt": "-",
    "jan_salary_am": "-",
}

# 전사합계 행 — 1인평균급여/연간급여총액이 채워진다.
_TOTAL_ROW = {
    "rcept_no": "20250311001085",
    "sexdstn": "남",
    "fo_bbm": "전사합계",
    "rgllbr_co": "94,416",
    "cnttk_co": "497",
    "sm": "94,913",
    "avrg_cnwk_sdytrn": "13.4",
    "fyer_salary_totamt": "12,863,945,000,000",
    "jan_salary_am": "139,000,000",
}


class FakeEmployeeClient:
    def __init__(self, response):
        self.response = response
        self.calls = []

    async def fetch_employees(self, *, corp_code, bsns_year, reprt_code):
        self.calls.append((corp_code, bsns_year, reprt_code))
        return self.response


class FakeCorpCodeRepository:
    def __init__(self, row):
        self.row = row

    async def get_corp_code_by_ticker(self, ticker):
        return self.row


class DartEmployeeCollectorTest(unittest.IsolatedAsyncioTestCase):
    async def _collect(self, response, *, corp_row={"corp_code": "00126380"}):
        client = FakeEmployeeClient(response)
        collector = DartEmployeeCollector(
            api_key="key",
            corp_code_repository=FakeCorpCodeRepository(corp_row),
            client=client,
        )
        stats = await collector.collect(
            stock_code="005930", bsns_year=2024, reprt_code="11011"
        )
        return client, stats

    async def test_parses_segment_row_and_injects_query_dimensions(self):
        client, stats = await self._collect(_ok_response([_SEGMENT_ROW]))

        self.assertEqual(client.calls, [("00126380", 2024, "11011")])
        self.assertEqual(len(stats), 1)
        stat = stats[0]
        self.assertEqual(stat["corp_code"], "00126380")
        self.assertEqual(stat["rcept_no"], "20250311001085")
        self.assertEqual(stat["bsns_year"], 2024)
        self.assertEqual(stat["reprt_code"], "11011")
        self.assertEqual(stat["segment"], "DX")
        self.assertEqual(stat["sex"], "남")
        self.assertEqual(stat["headcount"], 38291)
        self.assertEqual(stat["regular_count"], 37953)
        self.assertEqual(stat["contract_count"], 338)
        self.assertEqual(stat["avg_tenure_years"], 16.9)
        self.assertEqual(stat["line_seq"], 0)
        # 사업부문별 행의 급여 필드 '-' → None 정규화
        self.assertIsNone(stat["avg_salary_krw"])
        self.assertIsNone(stat["salary_total_krw"])

    async def test_total_row_carries_salary_fields(self):
        _, stats = await self._collect(_ok_response([_TOTAL_ROW]))

        stat = stats[0]
        self.assertEqual(stat["segment"], "전사합계")
        self.assertEqual(stat["avg_salary_krw"], 139000000)
        self.assertEqual(stat["salary_total_krw"], 12863945000000)

    async def test_segment_and_sex_distinguish_rows(self):
        female = {**_SEGMENT_ROW, "sexdstn": "여", "sm": "12,520"}
        _, stats = await self._collect(_ok_response([_SEGMENT_ROW, female]))

        self.assertEqual(len(stats), 2)
        # 자연키 차원(segment, sex)으로 두 행이 구분된다.
        self.assertEqual([(s["segment"], s["sex"]) for s in stats], [("DX", "남"), ("DX", "여")])
        # 서로 다른 (segment, sex) → 각자 첫 행이므로 line_seq 0
        self.assertEqual([s["line_seq"] for s in stats], [0, 0])

    async def test_line_seq_distinguishes_same_segment_sex_rows(self):
        # 같은 (segment, sex) 가 한 보고서에서 2행으로 나오는 비표준 공시 → line_seq 0, 1
        row2 = {**_SEGMENT_ROW, "sm": "100"}
        _, stats = await self._collect(_ok_response([_SEGMENT_ROW, row2]))

        self.assertEqual(len(stats), 2)
        self.assertEqual([s["line_seq"] for s in stats], [0, 1])
        # 자연키 나머지 차원은 동일, line_seq 만 다름
        self.assertEqual(stats[0]["segment"], stats[1]["segment"])
        self.assertEqual(stats[0]["sex"], stats[1]["sex"])

    async def test_no_data_returns_empty(self):
        _, stats = await self._collect({"status": "013"})
        self.assertEqual(stats, [])

    async def test_error_status_raises(self):
        with self.assertRaises(DartApiError):
            await self._collect({"status": "020", "message": "rate limit"})

    async def test_unmapped_ticker_raises(self):
        with self.assertRaises(DartApiError):
            await self._collect(_ok_response([_SEGMENT_ROW]), corp_row=None)

    async def test_missing_rcept_no_passes_through_for_repository_filter(self):
        # 필수키(rcept_no) 누락 행을 수집기는 버리지 않고 빈 채로 통과 → 리포지토리 필터가 폐기.
        bad = {**_SEGMENT_ROW, "rcept_no": ""}
        _, stats = await self._collect(_ok_response([bad]))
        self.assertEqual(len(stats), 1)
        self.assertEqual(stats[0]["rcept_no"], "")


class _QueuedJsonClient(DartEmployeeClient):
    """_get_json 응답을 큐로 주입해 클라이언트 재시도 로직만 검증한다."""

    def __init__(self, responses, **kwargs):
        super().__init__(
            api_key="k", retry_backoff_seconds=0, min_request_interval_sec=0, **kwargs
        )
        self._responses = list(responses)
        self.json_calls = 0

    def _get_json(self, url):
        self.json_calls += 1
        return self._responses.pop(0)


class DartEmployeeClientRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_retries_body_rate_limit_status_then_succeeds(self):
        client = _QueuedJsonClient(
            [{"status": "020", "message": "rate limit"}, _ok_response([_TOTAL_ROW])]
        )
        response = await client.fetch_employees(
            corp_code="00126380", bsns_year=2024, reprt_code="11011"
        )
        self.assertEqual(response["status"], "000")
        self.assertEqual(client.json_calls, 2)

    async def test_raises_after_retries_exhausted(self):
        client = _QueuedJsonClient([{"status": "020"}, {"status": "020"}], max_retries=1)
        with self.assertRaises(DartApiError):
            await client.fetch_employees(
                corp_code="00126380", bsns_year=2024, reprt_code="11011"
            )
        self.assertEqual(client.json_calls, 2)


class EmployeeHelpersTest(unittest.TestCase):
    def test_to_int_strips_commas_and_handles_blank(self):
        self.assertEqual(_to_int("38,291"), 38291)
        self.assertEqual(_to_int("12,863,945,000,000"), 12863945000000)
        self.assertIsNone(_to_int(""))
        self.assertIsNone(_to_int("-"))
        self.assertIsNone(_to_int(None))

    def test_to_ratio_parses_tenure(self):
        self.assertEqual(_to_ratio("16.9"), 16.9)
        self.assertIsNone(_to_ratio("-"))
        self.assertIsNone(_to_ratio(None))

    def test_to_ratio_extracts_leading_number_from_unit_suffix(self):
        # 비표준 공시의 단위 접미사도 선두 숫자를 추출한다(데이터 유실 방지).
        self.assertEqual(_to_ratio("16.9년"), 16.9)
        self.assertEqual(_to_ratio("16년 9개월"), 16.0)
        self.assertEqual(_to_ratio("약 16.9"), 16.9)
        self.assertIsNone(_to_ratio("정보없음"))


if __name__ == "__main__":
    unittest.main()
