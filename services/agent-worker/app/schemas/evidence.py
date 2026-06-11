from dataclasses import dataclass, field
from typing import Any, Literal

SourceType = Literal["DART", "REPORT", "HIRING", "PATENT", "DATALAB", "PRICE"]


@dataclass(frozen=True)
class RawEvidence:
    source: SourceType
    stock_code: str
    title: str
    content: str
    published_at: str | None = None
    url: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
