from app.collectors.report.pdf_downloader import make_report_storage_key, make_s3_key


def test_make_filename_uses_sanitized_firm_name_for_unknown_firms():
    filename = make_report_storage_key("005930", {
        "firm": "하나증권",
        "date": "2026.06.18",
        "report_type": "earnings_review",
        "source_hash": "a" * 64,
    })

    assert filename == "reports/005930/20260618_hana_aaaaaaaa.pdf"


def test_make_report_storage_key_uses_source_hash_to_avoid_collisions():
    first = make_report_storage_key("005930", {
        "firm": "하나증권",
        "date": "2026.06.18",
        "report_type": "earnings_review",
        "source_hash": "a" * 64,
    })
    second = make_report_storage_key("005930", {
        "firm": "하나증권",
        "date": "2026.06.18",
        "report_type": "earnings_review",
        "source_hash": "b" * 64,
    })

    assert first != second
    assert second == "reports/005930/20260618_hana_bbbbbbbb.pdf"


def test_legacy_make_s3_key_keeps_existing_cli_contract():
    assert make_s3_key("005930", "hana_20260618_er.pdf") == "reports/005930/hana_20260618_er.pdf"
