from app.collectors.report.pdf_downloader import make_filename


def test_make_filename_uses_sanitized_firm_name_for_unknown_firms():
    filename = make_filename({
        "firm": "하나증권",
        "date": "2026.06.18",
        "report_type": "earnings_review",
    })

    assert filename == "hana_20260618_er.pdf"
