"""HIRING 수집기 패키지 — 채용공고 수집 (사람인/잡코리아/공식 사이트)."""
from .base_collector import BaseCollector, get_target_companies
from .keyword_generator import HiringKeywordGenerator
from .mock_collector import MockCollector
from .multi_source_crawler import MultiSourceCrawler
from .web_crawler import WebCrawler

__all__ = [
    "BaseCollector",
    "get_target_companies",
    "HiringKeywordGenerator",
    "MockCollector",
    "MultiSourceCrawler",
    "WebCrawler",
]
