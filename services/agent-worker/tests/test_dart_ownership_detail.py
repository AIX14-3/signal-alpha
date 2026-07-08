"""DART 세부변동내역 파서 테스트 — 실제 문서(공시 본문) SGML 구조 기반 픽스처.

셀 의미 코드(AUNIT="RPT_RSN"·ACODE="ACI_AMT2")로 거래유형·단가를 결정론 추출하는지 검증.
"""

import unittest

from app.collectors.dart.ownership_detail import (
    MARKET_TRADE_TYPES,
    classify_report,
    parse_detail_rows,
)


def _doc(*rows: str) -> str:
    body = "\n".join(rows)
    return f"""<DOCUMENT><TITLE>세부변동내역</TITLE>
<TABLE ACLASS="EXTRACTION"><THEAD><TR><TH>보고사유</TH><TH>변동일</TH></TR></THEAD>
<TBODY>
{body}
</TBODY></TABLE></DOCUMENT>"""


def _row(code: str, text: str, price: str) -> str:
    return (
        '<TR ACOPY="Y" ADELETE="N">'
        f'<TU ALIGN="CENTER" AUNIT="RPT_RSN" AUNITVALUE="{code}">{text}</TU>'
        '<TU ALIGN="CENTER" AUNIT="MDF_DM" AUNITVALUE="20240729">2024년 07월 29일</TU>'
        '<TE ALIGN="RIGHT" ACODE="MDF_STK_CNT">100</TE>'
        f'<TE ALIGN="RIGHT" ACODE="ACI_AMT2">{price}</TE>'
        "</TR>"
    )


class ParseDetailRowsTest(unittest.TestCase):
    def test_extracts_onmarket_buy_with_price(self):
        rows = parse_detail_rows(_doc(_row("01", "장내매수(+)", "73,400")))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].trade_type, "onmarket_buy")
        self.assertEqual(rows[0].unit_price, 73400.0)
        self.assertIn(rows[0].trade_type, MARKET_TRADE_TYPES)

    def test_onmarket_sell(self):
        rows = parse_detail_rows(_doc(_row("02", "장내매도(-)", "79,100")))
        self.assertEqual(rows[0].trade_type, "onmarket_sell")

    def test_gift_and_option_are_non_market(self):
        rows = parse_detail_rows(
            _doc(_row("34", "증여(-)", "-"), _row("58", "주식매수선택권(+)", "-"))
        )
        self.assertEqual([r.trade_type for r in rows], ["gift", "stock_option"])
        self.assertNotIn(rows[0].trade_type, MARKET_TRADE_TYPES)

    def test_period_treated_as_thousands_separator(self):
        rows = parse_detail_rows(_doc(_row("01", "장내매수(+)", "80.400")))
        self.assertEqual(rows[0].unit_price, 80400.0)

    def test_parenthesized_price_is_none(self):
        rows = parse_detail_rows(_doc(_row("01", "장내매수(+)", "( 81100000 )")))
        self.assertIsNone(rows[0].unit_price)

    def test_text_fallback_for_unmapped_code(self):
        # 코드 미매핑(00)이라도 표시텍스트로 장외 판정.
        rows = parse_detail_rows(_doc(_row("00", "장외매도(-)", "-")))
        self.assertEqual(rows[0].trade_type, "offmarket")

    def test_no_table_returns_empty(self):
        self.assertEqual(parse_detail_rows("<DOCUMENT>no detail here</DOCUMENT>"), [])
        self.assertEqual(parse_detail_rows(""), [])


class ClassifyReportTest(unittest.TestCase):
    def test_single_onmarket_buy(self):
        rows = parse_detail_rows(_doc(_row("01", "장내매수(+)", "73,400"), _row("01", "장내매수(+)", "73,500")))
        tt, price = classify_report(rows)
        self.assertEqual(tt, "onmarket_buy")
        self.assertEqual(price, 73400.0)  # 첫 유효 시장 단가

    def test_buy_and_sell_mix_is_mixed(self):
        rows = parse_detail_rows(_doc(_row("01", "장내매수(+)", "73,400"), _row("02", "장내매도(-)", "79,100")))
        tt, _ = classify_report(rows)
        self.assertEqual(tt, "mixed")

    def test_market_plus_nonmarket_is_mixed(self):
        rows = parse_detail_rows(_doc(_row("01", "장내매수(+)", "73,400"), _row("34", "증여(-)", "-")))
        tt, _ = classify_report(rows)
        self.assertEqual(tt, "mixed")

    def test_single_gift(self):
        rows = parse_detail_rows(_doc(_row("34", "증여(-)", "-")))
        tt, price = classify_report(rows)
        self.assertEqual(tt, "gift")
        self.assertIsNone(price)

    def test_empty_rows(self):
        self.assertEqual(classify_report([]), (None, None))


if __name__ == "__main__":
    unittest.main()
