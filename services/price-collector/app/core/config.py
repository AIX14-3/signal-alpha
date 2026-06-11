from functools import lru_cache
from os import getenv


class Settings:
    def __init__(self) -> None:
        self.service_name = getenv("SERVICE_NAME", "price-collector")
        self.version = getenv("SERVICE_VERSION", "0.2.0")
        self.database_url = getenv("DATABASE_URL")

        # Kiwoom REST API (App Key/Secret + OAuth). Works on Linux/Docker —
        # no Windows COM dependency. Mock domain by default because the
        # currently issued key is a paper-trading key (expires 2026-09-06).
        # Switch to https://api.kiwoom.com once a production key is issued.
        self.kiwoom_app_key = getenv("KIWOOM_APP_KEY", "")
        self.kiwoom_app_secret = getenv("KIWOOM_APP_SECRET", "")
        self.kiwoom_api_base = getenv("KIWOOM_API_BASE", "https://mockapi.kiwoom.com").rstrip("/")
        self.kiwoom_timeout_seconds = float(getenv("KIWOOM_TIMEOUT_SECONDS", "10"))
        # Kiwoom enforces request-rate limits; keep a minimum gap between calls.
        self.kiwoom_min_request_interval_sec = float(
            getenv("KIWOOM_MIN_REQUEST_INTERVAL_SEC", "0.25")
        )

        # Intraday polling cadence (seconds between full target sweeps).
        self.poll_interval_sec = float(getenv("PRICE_POLL_INTERVAL_SEC", "60"))
        # Wait this long after market close before fetching confirmed
        # investor-flow figures (they settle after the session ends).
        self.flow_delay_after_close_min = int(getenv("PRICE_FLOW_DELAY_AFTER_CLOSE_MIN", "30"))

        self.market_open = getenv("MARKET_OPEN", "09:00")
        self.market_close = getenv("MARKET_CLOSE", "15:30")


@lru_cache
def get_settings() -> Settings:
    return Settings()
