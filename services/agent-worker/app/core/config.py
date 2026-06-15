from functools import lru_cache
from os import getenv
from pathlib import Path

from dotenv import load_dotenv

# 프로젝트 루트의 .env 로드
load_dotenv(Path(__file__).resolve().parents[4] / ".env")


class Settings:
    def __init__(self) -> None:
        self.service_name = getenv("SERVICE_NAME", "agent-worker")
        self.version = getenv("SERVICE_VERSION", "0.1.0")
        self.database_url = getenv("DATABASE_URL")
        self.parsed_reports_path: Path = (
            Path(__file__).resolve().parents[4] / "data" / "parsed_reports.json"
        )
        self.collector_version = getenv("COLLECTOR_VERSION", "1.0")
        self.dart_api_key = getenv("DART_API_KEY", "")
        self.dart_base_url = getenv("DART_BASE_URL", "https://opendart.fss.or.kr/api")
        self.dart_timeout_seconds = int(getenv("DART_TIMEOUT_SECONDS", "10"))
        self.dart_page_size = int(getenv("DART_PAGE_SIZE", "100"))
        self.dart_fetch_documents = _env_bool("DART_FETCH_DOCUMENTS", default=True)
        self.dart_max_retries = int(getenv("DART_MAX_RETRIES", "2"))
        self.dart_retry_backoff_seconds = float(getenv("DART_RETRY_BACKOFF_SECONDS", "0.5"))
        self.dart_use_llm = _env_bool("DART_USE_LLM", default=False)
        self.dart_llm_high_impact_only = _env_bool("DART_LLM_HIGH_IMPACT_ONLY", default=True)
        self.dart_llm_provider = getenv("DART_LLM_PROVIDER", "gemini").strip().lower()
        self.dart_llm_model = getenv("DART_LLM_MODEL", "")
        self.dart_llm_timeout_seconds = float(getenv("DART_LLM_TIMEOUT_SECONDS", "20"))
        self.openai_api_key = getenv("OPENAI_API_KEY", "")
        self.openai_base_url = getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.gemini_api_key = getenv("GEMINI_API_KEY", "")
        self.gemini_base_url = getenv(
            "GEMINI_BASE_URL",
            "https://generativelanguage.googleapis.com/v1beta",
        )
        self.aws_access_key_id = getenv("AWS_ACCESS_KEY_ID", "")
        self.aws_secret_access_key = getenv("AWS_SECRET_ACCESS_KEY", "")
        self.aws_region = getenv("AWS_REGION", "ap-northeast-2")
        self.s3_report_bucket = getenv("S3_REPORT_BUCKET", "signal-alpha-reports")

        self.kipris_api_key = getenv("KIPRIS_API_KEY", "")
        self.kipris_timeout_seconds = int(getenv("KIPRIS_TIMEOUT_SECONDS", "15"))
        self.kipris_page_size = int(getenv("KIPRIS_PAGE_SIZE", "100"))
        self.naver_client_id = getenv("NAVER_CLIENT_ID", "")
        self.naver_client_secret = getenv("NAVER_CLIENT_SECRET", "")
        self.naver_datalab_timeout_seconds = int(getenv("NAVER_DATALAB_TIMEOUT_SECONDS", "15"))

        # ── Realtime price collector (Kiwoom REST, agent-worker 내장 데몬) ──
        self.price_collector_enabled = _env_bool("PRICE_COLLECTOR_ENABLED", default=True)
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
        self.price_poll_interval_sec = float(getenv("PRICE_POLL_INTERVAL_SEC", "60"))
        # Wait this long after market close before fetching confirmed
        # investor-flow figures (they settle after the session ends).
        self.price_flow_delay_after_close_min = int(
            getenv("PRICE_FLOW_DELAY_AFTER_CLOSE_MIN", "30")
        )
        self.market_open = getenv("MARKET_OPEN", "09:00")
        self.market_close = getenv("MARKET_CLOSE", "15:30")


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _env_bool(name: str, *, default: bool) -> bool:
    value = getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
