"""HIRING 수집기 패키지 — 채용공고 수집 (사람인/잡코리아/공식 사이트)."""
from .base_collector import BaseCollector
from .keyword_generator import HiringKeywordGenerator
from .mock_collector import MockCollector
from .multi_source_crawler import MultiSourceCrawler
from .web_crawler import WebCrawler

__all__ = [
    "BaseCollector",
    "HiringKeywordGenerator",
    "MockCollector",
    "MultiSourceCrawler",
    "WebCrawler",
]
