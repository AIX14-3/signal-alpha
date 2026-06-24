from datetime import datetime

from app.collectors.report.crawler import parse_reports


def test_parse_reports_keeps_reports_from_any_securities_firm():
    html = """
    <html>
      <body>
        <table class="type_1">
          <tr>
            <td>삼성전자</td>
            <td><a href="company_read.naver?nid=1">삼성전자 기업분석 업데이트</a></td>
            <td>한국투자증권</td>
            <td><a href="https://example.com/report.pdf">PDF</a></td>
            <td>25.08.05</td>
            <td>10</td>
          </tr>
        </table>
      </body>
    </html>
    """

    reports, stop_paging = parse_reports(
        html,
        datetime(2025, 8, 1),
        datetime(2025, 8, 7, 23, 59, 59),
    )

    assert stop_paging is False
    assert len(reports) == 1
    assert reports[0]["firm"] == "한국투자증권"
