"""SEC CIK 유틸 (순수 함수, I/O 없음).

DART의 corp_code에 대응하는 CIK 처리. 하드코딩 없이 company_tickers.json을
파싱해 티커→CIK 매핑을 만든다.
"""

from __future__ import annotations

from typing import Any


def format_cik10(value: Any) -> str:
    """CIK를 zero-padded 10자리 문자열로 (예: 1045810 → '0001045810')."""
    text = str(value).strip()
    if text.isdigit():
        return f"{int(text):010d}"
    return text


def parse_ticker_map(payload: Any) -> dict[str, int]:
    """company_tickers.json → {TICKER(대문자): CIK(int)}.

    형태: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
    """
    mapping: dict[str, int] = {}
    rows = payload.values() if isinstance(payload, dict) else payload
    for row in rows:
        ticker = row.get("ticker")
        cik = row.get("cik_str")
        if ticker is None or cik is None:
            continue
        mapping[str(ticker).upper()] = int(cik)
    return mapping
