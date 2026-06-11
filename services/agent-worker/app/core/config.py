from functools import lru_cache
from os import getenv


class Settings:
    def __init__(self) -> None:
        self.service_name = getenv("SERVICE_NAME", "agent-worker")
        self.version = getenv("SERVICE_VERSION", "0.1.0")
        self.database_url = getenv("DATABASE_URL")
        self.collector_version = getenv("COLLECTOR_VERSION", "1.0")
        self.dart_api_key = getenv("DART_API_KEY", "")
        self.dart_base_url = getenv("DART_BASE_URL", "https://opendart.fss.or.kr/api")
        self.dart_timeout_seconds = int(getenv("DART_TIMEOUT_SECONDS", "10"))
        self.dart_page_size = int(getenv("DART_PAGE_SIZE", "100"))
        self.kipris_api_key = getenv("KIPRIS_API_KEY", "")
        self.kipris_timeout_seconds = int(getenv("KIPRIS_TIMEOUT_SECONDS", "15"))
        self.kipris_page_size = int(getenv("KIPRIS_PAGE_SIZE", "100"))
        self.naver_client_id = getenv("NAVER_CLIENT_ID", "")
        self.naver_client_secret = getenv("NAVER_CLIENT_SECRET", "")
        self.naver_datalab_timeout_seconds = int(getenv("NAVER_DATALAB_TIMEOUT_SECONDS", "15"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
