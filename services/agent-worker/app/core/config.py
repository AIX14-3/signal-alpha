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
        # ── L1 정형 재무 수집 (fnlttSinglAcntAll → dart_financial_facts) ──
        self.dart_financials_lookback_years = int(getenv("DART_FINANCIALS_LOOKBACK_YEARS", "3"))
        self.dart_financials_reprt_codes = _env_list(
            "DART_FINANCIALS_REPRT_CODES",
            default=["11011", "11012", "11013", "11014"],
        )
        self.dart_financials_fs_priority = _env_list(
            "DART_FINANCIALS_FS_PRIORITY", default=["CFS", "OFS"]
        )
        # OpenDART 분당 호출 제한 대비 요청 간 최소 간격(초).
        self.dart_financials_min_request_interval_sec = float(
            getenv("DART_FINANCIALS_MIN_REQUEST_INTERVAL_SEC", "0.2")
        )
        self.dart_use_llm = _env_bool("DART_USE_LLM", default=False)
        self.dart_llm_high_impact_only = _env_bool("DART_LLM_HIGH_IMPACT_ONLY", default=True)
        self.dart_llm_provider = getenv("DART_LLM_PROVIDER", "gemini").strip().lower()
        self.dart_llm_model = getenv("DART_LLM_MODEL", "")
        self.dart_llm_timeout_seconds = float(getenv("DART_LLM_TIMEOUT_SECONDS", "20"))
        # Report RAG agent LLM 종합 — provider/key는 아래 openai/gemini 공유 설정 재사용.
        self.report_use_llm = _env_bool("REPORT_USE_LLM", default=False)
        self.report_llm_provider = getenv("REPORT_LLM_PROVIDER", "gemini").strip().lower()
        self.report_llm_model = getenv("REPORT_LLM_MODEL", "")
        self.report_llm_timeout_seconds = float(getenv("REPORT_LLM_TIMEOUT_SECONDS", "20"))
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

        # ── Hiring 크롤러 resilience (공용 fetch 헬퍼: sites/http.py) ──
        # 일시적 timeout·5xx·커넥션오류를 지수 백오프로 재시도한다(4xx는 비재시도).
        self.hiring_timeout_seconds = float(getenv("HIRING_TIMEOUT_SECONDS", "10"))
        self.hiring_max_retries = int(getenv("HIRING_MAX_RETRIES", "2"))
        self.hiring_retry_backoff_seconds = float(getenv("HIRING_RETRY_BACKOFF_SECONDS", "0.5"))
        # ── Hiring 크롤러 anti-block (UA 로테이션 + 429/403 적응형 백오프) ──
        # 데스크톱 전용 UA 풀(모바일 금지 — m.* 모바일 레이아웃이 파싱을 깨뜨림).
        self.hiring_ua_pool = _env_list(
            "HIRING_UA_POOL",
            default=[
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0",
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 "
                "(KHTML, like Gecko) Version/17.2 Safari/605.1.15",
            ],
        )
        # 429 Retry-After/지수 백오프의 상한(초) — 악성/비정상 대기로 워커가 무한 수면하는 것 방어.
        self.hiring_rate_limit_max_backoff_seconds = float(
            getenv("HIRING_RATE_LIMIT_MAX_BACKOFF_SECONDS", "30")
        )

        # ── SEC EDGAR (해외/미국 공시 수집) ──
        # SEC는 연락처가 담긴 User-Agent를 요구한다(없으면 차단). 운영 시 팀 공용 주소로 교체.
        self.sec_user_agent = getenv(
            "SEC_USER_AGENT", "signal-alpha (contact: biop99999@gmail.com)"
        )
        self.sec_base_url = getenv("SEC_BASE_URL", "https://data.sec.gov").rstrip("/")
        self.sec_ticker_map_url = getenv(
            "SEC_TICKER_MAP_URL", "https://www.sec.gov/files/company_tickers.json"
        )
        self.sec_timeout_seconds = float(getenv("SEC_TIMEOUT_SECONDS", "15"))
        # SEC fair-access(~10 req/s) 준수를 위한 요청 간 최소 간격.
        self.sec_min_request_interval_sec = float(getenv("SEC_MIN_REQUEST_INTERVAL_SEC", "0.2"))
        self.sec_max_retries = int(getenv("SEC_MAX_RETRIES", "2"))
        # 수집 대상 폼 화이트리스트(쉼표 구분). 빈 값이면 전체 폼.
        self.sec_form_whitelist = _env_list(
            "SEC_FORM_WHITELIST",
            default=["8-K", "10-K", "10-Q", "4", "SC 13D", "SC 13G", "20-F", "6-K"],
        )
        # 수집 대상 기업(티커, 쉼표 구분). 빈 값이면 collectors/sec/targets.py의
        # 기본 유니버스(AI 선도주 보드)를 사용한다. 나중에 DB로 옮길 수 있다.
        self.sec_target_tickers = _env_list("SEC_TARGET_TICKERS", default=[])

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

        # ── Hiring 운영 알림 + self-healing 데몬 (Phase 5) ──
        # collector_runs 통계 기반 임계 판정 → Discord Embed 알림. sweep/reconcile 자동화.
        # 기본 off(price 데몬 관례). 단일 uvicorn 워커 전제(advisory lock으로 중복 기동 방지).
        self.hiring_ops_daemon_enabled = _env_bool("HIRING_OPS_DAEMON_ENABLED", default=False)
        # 빈 값이면 알림은 no-op(데몬은 sweep/reconcile만 수행).
        self.discord_webhook_url = getenv("DISCORD_WEBHOOK_URL", "")
        self.hiring_ops_interval_sec = float(getenv("HIRING_OPS_INTERVAL_SEC", "300"))
        # 거부율(failed/collected) 임계 — 초과 run을 Discord로 알림.
        self.hiring_alert_failure_rate_threshold = float(
            getenv("HIRING_ALERT_FAILURE_RATE_THRESHOLD", "0.5")
        )
        self.hiring_ops_sweep_running_timeout_min = int(
            getenv("HIRING_OPS_SWEEP_RUNNING_TIMEOUT_MIN", "30")
        )
        self.hiring_ops_sweep_retrying_timeout_min = int(
            getenv("HIRING_OPS_SWEEP_RETRYING_TIMEOUT_MIN", "120")
        )
        self.hiring_ops_reconcile_limit = int(getenv("HIRING_OPS_RECONCILE_LIMIT", "100"))
        # 알림 대상 collector_type(쉼표 구분). run별 임계 판정에 사용.
        self.hiring_alert_collector_types = _env_list(
            "HIRING_ALERT_COLLECTOR_TYPES", default=["HIRING"]
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


def _env_bool(name: str, *, default: bool) -> bool:
    value = getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_list(name: str, *, default: list[str]) -> list[str]:
    value = getenv(name)
    if value is None:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]
