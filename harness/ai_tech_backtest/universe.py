"""AI-leader stock universe for the technical-analysis backtest.

Only PUBLICLY TRADED names are included. Deliberately excluded:
- OpenAI, Anthropic  → privately held, no market price data exists.
- "Google" is the same listed entity as Alphabet → kept once as GOOGL.

TSMC is collected via its US ADR symbol ``TSM`` on Toss.
Korean names use the 6-digit KRX code Toss expects.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Instrument:
    symbol: str          # Toss API symbol
    name: str
    market: str          # "US" | "KR"
    currency: str        # "USD" | "KRW"


UNIVERSE: list[Instrument] = [
    Instrument("MSFT", "Microsoft", "US", "USD"),
    Instrument("GOOGL", "Alphabet (Google)", "US", "USD"),
    Instrument("AMZN", "Amazon", "US", "USD"),
    Instrument("META", "Meta", "US", "USD"),
    Instrument("NVDA", "NVIDIA", "US", "USD"),
    Instrument("AMD", "AMD", "US", "USD"),
    Instrument("INTC", "Intel", "US", "USD"),
    Instrument("AVGO", "Broadcom", "US", "USD"),
    Instrument("ORCL", "Oracle", "US", "USD"),
    Instrument("TSM", "TSMC (US ADR)", "US", "USD"),
    Instrument("005930", "Samsung Electronics", "KR", "KRW"),
    Instrument("000660", "SK hynix", "KR", "KRW"),
]

# Names the user mentioned that cannot be analysed, surfaced in the report.
EXCLUDED = {
    "OpenAI": "비상장 — 주가 데이터 없음",
    "Anthropic": "비상장 — 주가 데이터 없음",
    "Google": "Alphabet(GOOGL)과 동일 상장사 — GOOGL로 통합",
}


def symbols() -> list[str]:
    return [i.symbol for i in UNIVERSE]


def by_symbol(symbol: str) -> Instrument:
    for i in UNIVERSE:
        if i.symbol == symbol:
            return i
    raise KeyError(symbol)
