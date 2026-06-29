"""Unit tests for canonical patent application_no (cross-source dedup key)."""
from __future__ import annotations

import pytest

from app.collectors.patent.application_no import canonicalize_application_no

# The same Samsung patent as each source delivers it.
KIPRIS_FORM = "1020210012345"      # 13 digits: type(10) + year(2021) + serial(0012345)
BIGQUERY_FORM = "KR-20210012345-A"  # country + 11-digit year+serial + kind code
CANONICAL = "20210012345"           # 11-digit year+serial core


def test_kipris_and_bigquery_forms_collapse_to_same_key():
    """The whole point: same patent, two sources, one dedup key."""
    assert canonicalize_application_no(KIPRIS_FORM) == canonicalize_application_no(BIGQUERY_FORM)
    assert canonicalize_application_no(KIPRIS_FORM) == CANONICAL


@pytest.mark.parametrize(
    "raw,expected",
    [
        (KIPRIS_FORM, CANONICAL),               # 13-digit KIPRIS → drop 2-digit type prefix
        (BIGQUERY_FORM, CANONICAL),             # KR-...-A → 11-digit core
        ("KR-1020210012345-A", CANONICAL),      # BigQuery that kept the type prefix → 13 → drop 2
        (" 1020210012345 ", CANONICAL),         # surrounding whitespace
        ("2020210012345", CANONICAL),           # utility-model type (20) collapses on year+serial too
    ],
)
def test_known_forms_normalize(raw, expected):
    assert canonicalize_application_no(raw) == expected


@pytest.mark.parametrize("raw", [None, "", "   "])
def test_empty_inputs_return_empty(raw):
    assert canonicalize_application_no(raw) == ""


def test_unknown_shape_falls_back_to_stable_upper():
    """Non-KR / unexpected shapes keep a stable, normalized key (no false merge)."""
    assert canonicalize_application_no("us-9999999-b2") == "US-9999999-B2"
    # Stable: same raw → same key (within-source idempotency preserved).
    assert canonicalize_application_no("xyz") == canonicalize_application_no("XYZ")


def test_distinct_patents_do_not_collapse():
    assert canonicalize_application_no("1020210012345") != canonicalize_application_no("1020210012346")
