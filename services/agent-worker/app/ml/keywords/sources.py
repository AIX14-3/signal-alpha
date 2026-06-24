"""Source adapters: dated text records for a ticker, point-in-time.

Every source yields ``TextRecord(ticker, avail_date, text)`` where ``avail_date``
is the date the text became PUBLIC (so features for period T can use only text
available by T — no look-ahead). Patent titles ship first; DART disclosure text
and hiring titles plug in later by implementing the same ``records()`` shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Iterable, Protocol, runtime_checkable


@dataclass(frozen=True)
class TextRecord:
    ticker: str
    avail_date: date  # when the text became public (point-in-time anchor)
    text: str


@runtime_checkable
class SourceAdapter(Protocol):
    def tickers(self) -> list[str]: ...
    def records(self, ticker: str) -> Iterable[TextRecord]: ...


def _parse_yyyymmdd(value: str | None) -> date | None:
    if not value or len(value) < 8:
        return None
    try:
        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except ValueError:
        return None


class PatentTitleSource:
    """Patent titles from ``backfill_patents_bigquery.py --out`` JSON.

    ``avail_date`` = ``publication_date`` (when the patent text became public),
    NOT filing_date — filing precedes publication by ~18 months, so using filing
    would leak titles into periods before they were knowable.
    """

    def __init__(self, json_path: str | Path) -> None:
        self._data: dict[str, list[dict]] = json.loads(
            Path(json_path).read_text(encoding="utf-8")
        )

    def tickers(self) -> list[str]:
        return list(self._data)

    def records(self, ticker: str) -> Iterable[TextRecord]:
        for rec in self._data.get(ticker, []):
            avail = _parse_yyyymmdd(rec.get("publication_date"))
            title = (rec.get("title") or "").strip()
            if avail and title:
                yield TextRecord(ticker=ticker, avail_date=avail, text=title)
