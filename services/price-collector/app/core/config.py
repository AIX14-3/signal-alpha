from functools import lru_cache
from os import getenv


class Settings:
    """Runtime configuration sourced from environment variables.

    ``DATABASE_URL`` follows the repo-wide convention used by the other
    services and points at the shared PostgreSQL instance the collector
    loads ``ohlcv_data`` into.
    """

    def __init__(self) -> None:
        self.service_name = getenv("SERVICE_NAME", "price-collector")
        self.version = getenv("SERVICE_VERSION", "0.1.0")
        self.database_url = getenv("DATABASE_URL", "")
        # Kiwoom limits TR requests to 5/sec and 100/min; 0.2s keeps us under.
        self.tr_delay_sec = float(getenv("KIWOOM_TR_DELAY_SEC", "0.2"))
        self.tr_max_per_minute = int(getenv("KIWOOM_TR_MAX_PER_MINUTE", "100"))
        # How many trailing trade days of daily candles to keep in sync.
        self.lookback_days = int(getenv("PRICE_LOOKBACK_DAYS", "120"))
        # 수정주가구분=1 reflects splits / bonus issues (spec constraint 4).
        self.use_adjusted_price = getenv("PRICE_USE_ADJUSTED", "1") != "0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
