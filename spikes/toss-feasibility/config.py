"""Spike configuration loaded from environment.

Mirrors the getenv + lru_cache convention used by
services/price-collector/app/core/config.py so that, if this spike
graduates into a real collector, the settings shape is already familiar.
"""

from __future__ import annotations

from functools import lru_cache
from os import getenv


class Settings:
    def __init__(self) -> None:
        # Toss Securities Open API (OAuth2 client-credentials).
        self.toss_client_id = getenv("TOSS_CLIENT_ID", "")
        self.toss_client_secret = getenv("TOSS_CLIENT_SECRET", "")
        self.toss_api_base = getenv(
            "TOSS_API_BASE", "https://openapi.tossinvest.com"
        ).rstrip("/")
        self.timeout_seconds = float(getenv("TOSS_TIMEOUT_SECONDS", "10"))
        # Stay under the documented MARKET_DATA_CHART limit (5 req/s) by default.
        self.min_request_interval_sec = float(
            getenv("TOSS_MIN_REQUEST_INTERVAL_SEC", "0.25")
        )

        # Sample symbols for the verification scenarios.
        self.kr_symbol = getenv("TOSS_SPIKE_KR_SYMBOL", "005930")  # 삼성전자
        self.us_symbol = getenv("TOSS_SPIKE_US_SYMBOL", "AAPL")


@lru_cache
def get_settings() -> Settings:
    return Settings()
