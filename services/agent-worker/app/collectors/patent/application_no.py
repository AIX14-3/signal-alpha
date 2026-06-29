"""Canonical application_no for cross-source patent dedup (KIPRIS ↔ BigQuery).

The same Korean patent is identified differently by each source:

  - KIPRIS:   ``1020210012345``      (13 digits: 2-digit application-type code
                                       ``10``=patent/``20``=utility + 4-digit year
                                       + 7-digit serial)
  - BigQuery: ``KR-20210012345-A``    (country prefix + 11-digit year+serial +
                                       kind code ``-A``)

Both reduce to the 11-digit ``year+serial`` core, which we use as the canonical
key. Feeding this (instead of the raw, source-native string) into
``make_source_hash`` makes the same patent collapse on the ``raw_documents``
``source_hash`` UNIQUE constraint regardless of which source delivered it — so
overlapping data is stored once, while each source's unique patents still land.
"""
from __future__ import annotations

import re

_NON_DIGITS = re.compile(r"\D+")


def canonicalize_application_no(raw: str | None) -> str:
    """Return a source-agnostic dedup key for a KR patent application number.

    13-digit (KIPRIS) → drop the 2-digit application-type prefix → 11-digit core.
    11-digit (BigQuery ``year+serial``) → already canonical.
    A BigQuery value that keeps the type prefix (13 digits after stripping
    ``KR``/``-A``/hyphens) converges to the same core too.

    Anything that doesn't match the KR shape falls back to the trimmed,
    upper-cased original: a *stable* key that preserves within-source idempotency
    without risking a false cross-source merge.
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text:
        return ""
    digits = _NON_DIGITS.sub("", text)
    if len(digits) == 13:
        # 2-digit application-type code + 4-digit year + 7-digit serial.
        return digits[2:]
    if len(digits) == 11:
        # 4-digit year + 7-digit serial — already the canonical core.
        return digits
    # Unknown shape — keep a stable normalized fallback (no false merge).
    return text.upper()
