from typing import Protocol

from app.schemas.evidence import RawEvidence, SourceType


class Collector(Protocol):
    source: SourceType

    async def collect(self, stock_code: str) -> list[RawEvidence]:
        """Collect raw evidence only; do not create direction, score, or summary."""
