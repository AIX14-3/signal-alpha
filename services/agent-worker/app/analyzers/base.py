from typing import Protocol

from app.schemas.evidence import RawEvidence, SourceType
from app.schemas.source_result import SourceResult


class Analyzer(Protocol):
    source: SourceType

    async def analyze(
        self,
        stock_code: str,
        evidence: list[RawEvidence]
    ) -> SourceResult:
        """Analyze previously collected evidence; do not call external source APIs."""
