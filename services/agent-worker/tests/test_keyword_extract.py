"""Unit tests for period-trending keyword extraction (Stage 2)."""

from __future__ import annotations

from datetime import date

from app.ml.keywords.extract import _STOPWORDS, _terms, extract_period_keywords, period_key
from app.ml.keywords.sources import TextRecord, _parse_yyyymmdd


def test_terms_filters_stopwords_and_builds_bigrams():
    terms = _terms("반도체 메모리 장치 제조 방법")
    assert "반도체" in terms and "메모리" in terms
    assert "장치" not in terms and "방법" not in terms  # generic stopwords dropped
    assert "반도체 메모리" in terms  # bigram of two non-stopword tokens
    assert "메모리 장치" not in terms  # bigram touching a stopword is skipped


def test_period_key_buckets():
    assert period_key(date(2017, 3, 9), months=6) == "2017H1"
    assert period_key(date(2017, 9, 9), months=6) == "2017H2"
    assert period_key(date(2017, 4, 1), months=3) == "2017Q2"
    assert period_key(date(2017, 4, 1), months=12) == "2017"


def test_parse_yyyymmdd():
    assert _parse_yyyymmdd("20160331") == date(2016, 3, 31)
    assert _parse_yyyymmdd("") is None
    assert _parse_yyyymmdd("bad") is None


def test_terms_extra_stopwords_extends_defaults():
    # extra_stopwords add to (not replace) the patent defaults.
    base = _terms("보고서 반도체")
    assert "보고서" in base and "반도체" in base
    pruned = _terms("보고서 반도체", frozenset({"보고서"}) | _STOPWORDS)
    assert "보고서" not in pruned and "반도체" in pruned


def test_extract_period_keywords_honors_extra_stopwords():
    recs = [
        TextRecord("005930", date(2016, 1, 5), "보고서 공급계약"),
        TextRecord("005930", date(2016, 2, 5), "보고서 공급계약"),
        TextRecord("005930", date(2016, 3, 5), "보고서 공급계약"),
    ]
    kws = {k.keyword for k in extract_period_keywords(recs, extra_stopwords={"보고서"})}
    assert "공급계약" in kws and "보고서" not in kws


def test_dart_title_source_uses_rcept_dt_as_pit_anchor(tmp_path):
    import json

    from app.ml.keywords.sources import DartTitleSource

    path = tmp_path / "disc.json"
    path.write_text(
        json.dumps(
            {"005930": [{"rcept_dt": "20180312", "report_nm": "유상증자결정"},
                        {"rcept_dt": "bad", "report_nm": "drop me"},
                        {"rcept_dt": "20180401", "report_nm": ""}]},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    recs = list(DartTitleSource(str(path)).records("005930"))
    assert len(recs) == 1  # bad date and empty title dropped
    assert recs[0].avail_date == date(2018, 3, 12)
    assert recs[0].text == "유상증자결정"


def test_emerging_term_surfaces_in_its_period_not_before():
    recs: list[TextRecord] = []
    # 2016H1: "디스플레이" is the common theme; no HBM yet.
    for _ in range(6):
        recs.append(TextRecord("005930", date(2016, 2, 1), "디스플레이 패널 구동"))
    # 2022H1: "HBM" surges; "디스플레이" gone.
    for _ in range(6):
        recs.append(TextRecord("005930", date(2022, 2, 1), "HBM 적층 메모리"))

    kws = extract_period_keywords(recs, months=6, top_k=10, min_count=3)
    by_period: dict[str, list[str]] = {}
    for k in kws:
        by_period.setdefault(k.period, []).append(k.keyword)

    # HBM is a fresh surge in 2022H1 (absent from the prior baseline).
    assert "HBM" in by_period.get("2022H1", [])
    # It must NOT appear as a 2016 keyword (point-in-time: not knowable then).
    assert "HBM" not in by_period.get("2016H1", [])
    # A term that vanished by 2022 is not flagged emergent there.
    assert "디스플레이" not in by_period.get("2022H1", [])
    # first_avail_date is carried and ISO-formatted.
    hbm = next(k for k in kws if k.keyword == "HBM" and k.period == "2022H1")
    assert hbm.first_avail_date == "2022-02-01" and hbm.count == 6
