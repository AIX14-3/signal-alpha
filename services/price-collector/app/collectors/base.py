from typing import Protocol


class PriceSourceCollector(Protocol):
    tr_code: str
    output: str

    def collect(self, ticker: str, base_date: str | None = None):
        """Fetch and normalize one TR's rows; never derive analysis fields."""
