import unittest
from datetime import date

from app.collectors.dart.disclosure import DartApiError
from app.collectors.dart.ownership_api import (
    DartOwnershipClient,
    DartOwnershipCollector,
    _to_date,
    _to_int,
    _to_ratio,
)


def _ok_response(rows):
    return {"status": "000", "message": "정상", "list": rows}


_MAJOR_ROW = {
    "rcept_no": "20260315000123",
    "rcept_dt": "20260315",
    "repror": "국민연금공단",
    "stkqy": "70,000,000",
    "stkrt": "7.50",
    "stkqy_irds": "1,000,000",
    "stkrt_irds": "0.10",
    "report_tp": "장내매수",
}

_ELE_ROW = {
    "rcept_no": "20260320000456",
    "rcept_dt": "2026-03-20",
    "repror": "홍길동",
    "isu_exctv_ofcps": "대표이사",
    "sp_stock_lmp_cnt": "150,000",
    "sp_stock_lmp_irds_cnt": "-5,000",
}


class FakeOwnershipClient:
    def __init__(self, *, major, ele):
        self.major = major
        self.ele = ele
        self.calls = []

    async def fetch_majorstock(self, *, corp_code):
        self.calls.append(("major", corp_code))
        return self.major

    async def fetch_elestock(self, *, corp_code):
        self.calls.append(("ele", corp_code))
        return self.ele


class FakeCorpCodeRepository:
    def __init__(self, row):
        self.row = row

    async def get_corp_code_by_ticker(self, ticker):
        return self.row


class DartOwnershipCollectorTest(unittest.IsolatedAsyncioTestCase):
    async def _collect(self, *, major, ele):
        collector = DartOwnershipCollector(
            api_key="key",
            corp_code_repository=FakeCorpCodeRepository({"corp_code": "00126380"}),
            client=FakeOwnershipClient(major=major, ele=ele),
        )
        return collector, await collector.collect(stock_code="005930")

    async def test_parses_major_and_ele_into_unified_events(self):
        _, events = await self._collect(
            major=_ok_response([_MAJOR_ROW]), ele=_ok_response([_ELE_ROW])
        )

        self.assertEqual(len(events), 2)
        major = events[0]
        self.assertEqual(major["corp_code"], "00126380")
        self.assertEqual(major["rcept_no"], "20260315000123")
        self.assertEqual(major["holder_type"], "major")
        self.assertEqual(major["holder_name"], "국민연금공단")
        self.assertEqual(major["report_date"], date(2026, 3, 15))
        self.assertEqual(major["shares"], 70000000)
        self.assertEqual(major["ratio"], 7.5)
        self.assertEqual(major["shares_delta"], 1000000)

        ele = events[1]
        self.assertEqual(ele["holder_type"], "executive")
        self.assertEqual(ele["shares"], 150000)
        self.assertEqual(ele["shares_delta"], -5000)
        self.assertEqual(ele["report_reason"], "대표이사")
        # 서로 다른 보고자/접수번호 → 각자 첫 행이므로 line_seq 0
        self.assertEqual(major["line_seq"], 0)
        self.assertEqual(ele["line_seq"], 0)

    async def test_line_seq_distinguishes_same_holder_multiple_rows(self):
        # 한 접수번호에서 같은 보고자가 증권종류별로 2행 → line_seq 0, 1
        row2 = {**_ELE_ROW, "sp_stock_lmp_cnt": "50,000"}
        _, events = await self._collect(
            major={"status": "013"}, ele=_ok_response([_ELE_ROW, row2])
        )

        self.assertEqual(len(events), 2)
        self.assertEqual([e["line_seq"] for e in events], [0, 1])
        # 자연키 4요소는 동일, line_seq 만 다름
        self.assertEqual(events[0]["rcept_no"], events[1]["rcept_no"])
        self.assertEqual(events[0]["holder_name"], events[1]["holder_name"])

    async def test_elestock_main_shareholder_classification(self):
        # 직위(ofcps) 없고 주요주주 관계(isu_main_shrholdr)만 있으면 main_shareholder
        row = {**_ELE_ROW, "isu_exctv_ofcps": "-", "isu_main_shrholdr": "주요주주"}
        _, events = await self._collect(
            major={"status": "013"}, ele=_ok_response([row])
        )

        self.assertEqual(events[0]["holder_type"], "main_shareholder")
        self.assertEqual(events[0]["report_reason"], "주요주주")

    async def test_elestock_main_shareholder_takes_priority_over_executive(self):
        # 임원이면서 주요주주(겸직)면 희소·고신호인 main_shareholder 로 분류한다.
        # (elestock 은 거의 모든 행에 직위가 있어 직위 우선 시 주요주주를 잃는다 — 실데이터 확인)
        row = {**_ELE_ROW, "isu_exctv_ofcps": "부회장", "isu_main_shrholdr": "10%이상주주"}
        _, events = await self._collect(
            major={"status": "013"}, ele=_ok_response([row])
        )

        self.assertEqual(events[0]["holder_type"], "main_shareholder")

    async def test_no_data_returns_empty(self):
        _, events = await self._collect(
            major={"status": "013"}, ele={"status": "013"}
        )
        self.assertEqual(events, [])

    async def test_error_status_raises(self):
        with self.assertRaises(DartApiError):
            await self._collect(
                major={"status": "020", "message": "rate limit"}, ele={"status": "013"}
            )

    async def test_unparseable_rows_pass_through_for_repository_filter(self):
        # 보고자 누락 행을 수집기는 버리지 않고 빈 필수키로 통과시킨다 — 리포지토리 upsert
        # 필터가 폐기하고 sync 의 skipped_count 로 가시화되도록(리뷰 #3).
        bad = {**_MAJOR_ROW, "repror": ""}  # 보고자 누락
        _, events = await self._collect(
            major=_ok_response([bad]), ele={"status": "013"}
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["holder_name"], "")


class _QueuedJsonClient(DartOwnershipClient):
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


class DartOwnershipClientRetryTest(unittest.IsolatedAsyncioTestCase):
    async def test_retries_body_rate_limit_status_then_succeeds(self):
        client = _QueuedJsonClient(
            [{"status": "020", "message": "rate limit"}, _ok_response([_MAJOR_ROW])]
        )
        response = await client.fetch_majorstock(corp_code="00126380")
        self.assertEqual(response["status"], "000")
        self.assertEqual(client.json_calls, 2)  # 020 → 재시도 → 000

    async def test_raises_after_retries_exhausted(self):
        client = _QueuedJsonClient([{"status": "020"}, {"status": "020"}], max_retries=1)
        with self.assertRaises(DartApiError):
            await client.fetch_elestock(corp_code="00126380")
        self.assertEqual(client.json_calls, 2)


class OwnershipHelpersTest(unittest.TestCase):
    def test_to_int_strips_commas_and_handles_blank(self):
        self.assertEqual(_to_int("70,000,000"), 70000000)
        self.assertEqual(_to_int("-5,000"), -5000)
        self.assertIsNone(_to_int(""))
        self.assertIsNone(_to_int("-"))
        self.assertIsNone(_to_int(None))

    def test_to_ratio_parses_percent(self):
        self.assertEqual(_to_ratio("7.50"), 7.5)
        self.assertEqual(_to_ratio("0.1%"), 0.1)
        self.assertIsNone(_to_ratio("-"))
        self.assertIsNone(_to_ratio(None))

    def test_to_date_handles_formats(self):
        self.assertEqual(_to_date("20260315"), date(2026, 3, 15))
        self.assertEqual(_to_date("2026-03-15"), date(2026, 3, 15))
        self.assertEqual(_to_date("2026.03.15"), date(2026, 3, 15))
        self.assertIsNone(_to_date("2026"))
        self.assertIsNone(_to_date(None))


if __name__ == "__main__":
    unittest.main()
