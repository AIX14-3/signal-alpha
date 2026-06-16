"""SEC EDGAR 수집기 (해외/미국 공시). DART collectors의 미러 구조."""

from app.collectors.sec.cik_map import format_cik10, parse_ticker_map
from app.collectors.sec.filings import (
    SecEdgarClient,
    SecFiling,
    build_document_url,
    parse_recent_filings,
)
from app.collectors.sec.targets import (
    AI_LEADER_TARGETS,
    SecTarget,
    default_target_tickers,
    listed_targets,
    target_by_ticker,
)

__all__ = [
    "AI_LEADER_TARGETS",
    "SecEdgarClient",
    "SecFiling",
    "SecTarget",
    "build_document_url",
    "default_target_tickers",
    "format_cik10",
    "listed_targets",
    "parse_recent_filings",
    "parse_ticker_map",
    "target_by_ticker",
]
