"""EvidenceLoader contract.

Loaders are the *only* layer that touches the database for analysis: they read
the collector's raw detail tables and hand the rows to a pure analyzer inside
``RawEvidence.metadata`` (the same shape the PriceAnalyzer consumes). Keeping I/O
here means the analyzers stay pure/testable, and a future Normalizer can be
swapped in by replacing the loader without touching analyzer or aggregator code.
"""

from __future__ import annotations

from datetime import date
from typing import Protocol

from app.schemas.evidence import RawEvidence, SourceType


class EvidenceLoader(Protocol):
    source: SourceType

    async def load(
        self,
        *,
        stock_id: int,
        stock_code: str,
        as_of: date,
    ) -> list[RawEvidence]:
        """Read raw detail rows for one stock and wrap them as RawEvidence."""
