from functools import lru_cache
from os import getenv


class Settings:
    def __init__(self) -> None:
        self.service_name = getenv("SERVICE_NAME", "agent-worker")
        self.version = getenv("SERVICE_VERSION", "0.1.0")
        self.database_url = getenv("DATABASE_URL")
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


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _env_bool(name: str, *, default: bool) -> bool:
    value = getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
