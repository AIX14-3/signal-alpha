"""Tests for the HIRING precise source_name re-attribution (precision contract).

The expanded universe is matched broadly at ingest (recall), so the ML layer must
re-attribute postings to a ticker by EXACT normalized source_name match and drop
anything ambiguous — otherwise a substring fuzzy match would book e.g. 'SKC코오롱PI'
under 'SKC'. These tests pin that contract without touching a DB.
"""

from __future__ import annotations

from app.ml.research.hiring_db import _norm_name, unique_norm_map


def test_norm_name_strips_corp_suffix_space_and_case():
    assert _norm_name("(주)카카오") == _norm_name("카카오")
    assert _norm_name("SK 하이닉스") == _norm_name("SK하이닉스")
    assert _norm_name("NAVER") == "naver"


def test_unique_map_resolves_exact_name_and_short_name():
    rows = [
        (1, "NAVER", "네이버"),
        (2, "삼성전자", None),
    ]
    m = unique_norm_map(rows)
    assert m[_norm_name("네이버")] == 1      # via short_name
    assert m[_norm_name("naver")] == 1       # via name (case-insensitive)
    assert m[_norm_name("삼성전자")] == 2


def test_substring_name_is_not_mis_attributed():
    # 'SKC코오롱PI' must NOT collapse to 'SKC' (exact-only, no substring match).
    rows = [(1, "SKC", None), (2, "SK하이닉스", None)]
    m = unique_norm_map(rows)
    assert _norm_name("SKC코오롱PI") not in m
    assert m[_norm_name("SKC")] == 1


def test_ambiguous_name_shared_by_two_tickers_is_dropped():
    rows = [(1, "동일제강", None), (2, "동일제강", "동일")]
    m = unique_norm_map(rows)
    assert _norm_name("동일제강") not in m   # maps to {1,2} → ambiguous → excluded
    assert m[_norm_name("동일")] == 2        # unambiguous short_name still resolves


def test_blank_names_are_ignored():
    rows = [(1, "삼성전자", ""), (2, None, None)]
    m = unique_norm_map(rows)
    assert "" not in m
    assert m[_norm_name("삼성전자")] == 1
