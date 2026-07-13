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
        # 수집 DB (워커 소유 — stocks/signal_events/ml_inferences/meta_signals …).
        self.database_url = getenv("DATABASE_URL")
        # 백엔드(서비스) DB 발행용 DSN (#531 2-DB 물리 분리). 워커가 산출물을
        # 백엔드 DB로 publish 할 때만 사용. 미설정이면 발행 비활성(단일 DB 모드).
        self.backend_database_url = getenv("BACKEND_DATABASE_URL")
        # /internal/* 호출 공유 시크릿. 설정 시 X-Internal-Token 헤더 일치 요구(스케줄러 등
        # 신뢰된 호출자만 통과). 빈 값이면 검사 비활성(네트워크 격리에만 의존 — 기존 동작).
        # Empty values fail closed for /internal/* endpoints.
        self.internal_api_token = getenv("INTERNAL_API_TOKEN", "")
        self.parsed_reports_path: Path = (
            Path(__file__).resolve().parents[4] / "data" / "parsed_reports.json"
        )
        self.collector_version = getenv("COLLECTOR_VERSION", "1.0")
        self.dart_api_key = getenv("DART_API_KEY", "")
        self.dart_base_url = getenv("DART_BASE_URL", "https://opendart.fss.or.kr/api")
        self.dart_timeout_seconds = int(getenv("DART_TIMEOUT_SECONDS", "10"))
        self.dart_page_size = int(getenv("DART_PAGE_SIZE", "100"))
        self.dart_fetch_documents = _env_bool("DART_FETCH_DOCUMENTS", default=True)
        # 한 collect_dart 회차당 본문 다운로드 상한 — 무거운 문서 fetch 로 워커가 분 단위로
        # 묶이는 것을 막는다(작은 배치·공정 drain 과 병행). 상한 초과 공시는 메타데이터만 적재.
        # 증분 수집(last_end_de 이후)이라 평상시 회차는 작고, 첫 회차/백필만 상한이 작동한다.
        # 0 이하면 무제한(상한 없음).
        self.dart_max_documents = int(getenv("DART_MAX_DOCUMENTS", "30"))
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
        # ── L2 지분·내부자 수집 (majorstock/elestock → dart_ownership_events) ──
        self.dart_ownership_min_request_interval_sec = float(
            getenv("DART_OWNERSHIP_MIN_REQUEST_INTERVAL_SEC", "0.2")
        )
        # ── L3 임직원 현황 수집 (empSttus → dart_employee_stats) ──
        self.dart_employee_lookback_years = int(getenv("DART_EMPLOYEE_LOOKBACK_YEARS", "3"))
        # empSttus 는 사업보고서(11011)·반기(11012)에만 제출되고 분기(11013/11014)엔 거의 없으므로
        # 기본값을 둘로 제한해 무자료(013) 호출 낭비를 줄인다(financials 와 의도적으로 다름).
        self.dart_employee_reprt_codes = _env_list(
            "DART_EMPLOYEE_REPRT_CODES",
            default=["11011", "11012"],
        )
        self.dart_employee_min_request_interval_sec = float(
            getenv("DART_EMPLOYEE_MIN_REQUEST_INTERVAL_SEC", "0.2")
        )
        self.dart_use_llm = _env_bool("DART_USE_LLM", default=False)
        self.dart_llm_high_impact_only = _env_bool("DART_LLM_HIGH_IMPACT_ONLY", default=True)
        self.dart_llm_provider = getenv("DART_LLM_PROVIDER", "gemini").strip().lower()
        self.dart_llm_model = getenv("DART_LLM_MODEL", "")
        self.dart_llm_timeout_seconds = float(getenv("DART_LLM_TIMEOUT_SECONDS", "20"))
        # Report PDF parser LLM enrichment uses the shared OpenAI/Gemini settings below.
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
        self.report_storage_backend = getenv("REPORT_STORAGE_BACKEND", "gcs").strip().lower()
        self.gcp_project_id = getenv("GCP_PROJECT_ID", "")
        self.gcs_report_bucket = getenv("GCS_REPORT_BUCKET", "signal-alpha-reports")
        self.report_local_storage_dir = Path(
            getenv(
                "REPORT_LOCAL_STORAGE_DIR",
                str(Path(__file__).resolve().parents[4] / "data" / "report-storage"),
            )
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

        # ── 종목별 뉴스 데몬 (Naver News Search → backend DB stock_news, display-only) ──
        # guard 와 동일하게 BACKEND_DATABASE_URL 풀로 적재. 기본 off.
        self.news_enabled = _env_bool("NEWS_ENABLED", default=False)
        self.news_lookback_days = int(getenv("NEWS_LOOKBACK_DAYS", "14"))
        self.news_max_items = int(getenv("NEWS_MAX_ITEMS", "20"))
        self.news_poll_interval_sec = float(getenv("NEWS_POLL_INTERVAL_SEC", "900"))
        self.news_batch_size = int(getenv("NEWS_BATCH_SIZE", "20"))
        self.news_refresh_hours = float(getenv("NEWS_REFRESH_HOURS", "6"))
        self.news_fetch_timeout_seconds = float(getenv("NEWS_FETCH_TIMEOUT_SECONDS", "15"))

        # ── 뉴스 LLM 다이제스트 (종목별 종합 1줄, Claude Sonnet, display-only) ──
        # 관련도 규칙 1차 필터 후 후보를 Claude 1콜에 태워 영향도 선별+요약을 결합한다.
        # 신호 스코어링과 무관(signal_events/scoring 미접촉). 기본 off. 키 부재/실패 시
        # digest 없이 기존 뉴스 목록만 노출(폴백). 콜당 단가가 있어 후보 상한·dirty skip 필수.
        self.news_llm_enabled = _env_bool("NEWS_LLM_ENABLED", default=False)
        self.news_llm_provider = getenv("NEWS_LLM_PROVIDER", "anthropic")
        self.news_llm_model = getenv("NEWS_LLM_MODEL", "claude-sonnet-5")
        self.anthropic_api_key = getenv("ANTHROPIC_API_KEY", "")
        self.news_llm_timeout_seconds = float(getenv("NEWS_LLM_TIMEOUT_SECONDS", "20"))
        # LLM 에 넘길 관련도 규칙 후보 상한(토큰·비용 통제).
        self.news_digest_candidates = int(getenv("NEWS_DIGEST_CANDIDATES", "15"))
        # 종목 재요약 하한 간격(0=하한 없음; 수집이 잦아질 때 폭주 방지 옵션 3h).
        self.news_digest_min_interval_hours = float(
            getenv("NEWS_DIGEST_MIN_INTERVAL_HOURS", "0")
        )

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
        # ── Hiring 포털별 수집 on/off ──
        # 세 포털은 폴백 체인이 아니라 매번 전부 도는 합집합이다(multi_source_crawler.collect).
        # 그래서 한 소스가 0건이어도 다른 소스는 영향받지 않고, 개별로 끄는 게 안전하다.
        #
        # 사람인만 기본 off: 안티봇으로 헤드리스 접근이 IP 차단돼 전 기간 실적재 0건이다
        # (커밋 68cd180 "SARAMIN 전 기간 0건이 차단 무음처리 탓으로 확인됨"). 켜 두면 종목당
        # (별칭 수 × 페이지 로드 + rate-limit sleep)이 순수 낭비이고, 차단된 IP를 계속 두드린다.
        # 크롤러·차단 감지(is_blocked)는 그대로 남아 있으니, IP/프록시가 바뀌면 이 값만 true 로
        # 되돌려 재개한다(#162 프록시 로테이션).
        self.hiring_enable_saramin = _env_bool("HIRING_ENABLE_SARAMIN", default=False)
        self.hiring_enable_jobkorea = _env_bool("HIRING_ENABLE_JOBKOREA", default=True)
        self.hiring_enable_jasoseol = _env_bool("HIRING_ENABLE_JASOSEOL", default=True)

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
        # ── 파이프라인 큐 정지 알림(hiring 한정 → 파이프라인 전역) ──
        # ops 데몬이 매 틱 processing_queue 백로그(pending+retrying)를 본다. 임계 초과 + 직전 틱 대비
        # 미감소(드레인 정지)면 1회 알림. 구조적 self-heal 은 /health/live·스케줄러 하트비트가 담당하고,
        # 이 알림은 사람 인지용 보조. 0 이면 비활성.
        self.ops_queue_backlog_alert_threshold = int(
            getenv("OPS_QUEUE_BACKLOG_ALERT_THRESHOLD", "500")
        )
        # 최근 실패 급증 알림 임계(최근 윈도우 내 failed 수). 0 이면 비활성.
        self.ops_queue_failed_recent_alert_threshold = int(
            getenv("OPS_QUEUE_FAILED_RECENT_ALERT_THRESHOLD", "200")
        )
        self.ops_queue_failed_window_minutes = int(
            getenv("OPS_QUEUE_FAILED_WINDOW_MINUTES", "360")
        )

        # ── 큐 드레인 데몬 (워커 영역 완성 #11) ──
        # processing_queue 를 연속 소비해 수집→정규화→분석→집계→게이트→종합→발행(PUBLISH_SIGNALS)
        # 까지 끝단으로 흘린다. 기존 ops/price 데몬과 동일 패턴(advisory lock 단일 기동).
        # 기본 off(데몬 관례) — 운영/통합 인스턴스에서만 켠다.
        self.queue_drain_daemon_enabled = _env_bool("QUEUE_DRAIN_DAEMON_ENABLED", default=False)
        # 큐가 비었을 때(전 task_type idle) 다음 순회까지 대기 초. 작업이 있으면 쉬지 않고 계속 드레인.
        self.queue_drain_interval_sec = float(getenv("QUEUE_DRAIN_INTERVAL_SEC", "5"))
        # 드레인 데몬 liveness 임계(초). /health/live 는 데몬이 이 시간 넘게 사이클을 마치지 못하면
        # 503 → k8s 가 pod 재시작(라이브락/행 자가치유). cycles_completed 는 매 사이클 무조건
        # 증가하므로 last_finished_at 정체 = 정지 신호. 기본 = interval×6(넉넉히, 오탐 방지).
        self.queue_drain_liveness_max_stale_sec = float(
            getenv("QUEUE_DRAIN_LIVENESS_MAX_STALE_SEC", str(self.queue_drain_interval_sec * 6))
        )

        # ── 지정학 리스크 Kill-Switch 뉴스 감시 데몬 (guard) ──
        # GDELT 수집 → LLM 판정 → 차단 제안(advisory)/자동 차단(auto). guard_* 테이블은
        # backend DB 소유(관리자 수동 토글이 워커 없이도 동작해야 하므로) — 데몬은
        # BACKEND_DATABASE_URL 풀로만 쓴다(journal_outcomes 러너와 같은 워커→백엔드 계약).
        # 기본 off(데몬 관례). LLM 키는 공유 gemini_api_key 재사용.
        self.guard_enabled = _env_bool("GUARD_ENABLED", default=False)
        self.guard_poll_interval_sec = float(getenv("GUARD_POLL_INTERVAL_SEC", "900"))
        self.guard_keywords = _env_list(
            "GUARD_KEYWORDS",
            default=["war", "ceasefire", "sanctions", "military strike", "nuclear", "확전", "휴전"],
        )
        self.guard_news_max_articles = int(getenv("GUARD_NEWS_MAX_ARTICLES", "25"))
        self.guard_llm_model = getenv("GUARD_LLM_MODEL", "")
        self.guard_llm_timeout_seconds = float(getenv("GUARD_LLM_TIMEOUT_SECONDS", "20"))
        # 차단 제안/실행 임계 severity(0~100).
        self.guard_severity_threshold = int(getenv("GUARD_SEVERITY_THRESHOLD", "70"))
        # auto 모드가 스스로 걸 수 있는 scope 상한 — whole_site 는 언제나 사람 승인 필요.
        self.guard_auto_max_scope = getenv("GUARD_AUTO_MAX_SCOPE", "report_generation")
        # auto 모드 상태 변경 최소 간격(깜빡임 방지 쿨다운).
        self.guard_auto_cooldown_sec = float(getenv("GUARD_AUTO_COOLDOWN_SEC", "3600"))

        # ── 에피소드 아웃컴 리코더 (에이전트화 Wave 3 후속②) ──
        # 발행 후 실현 결과(forward return·direction hit)를 사후에 signal_episodes.outcome 에
        # 진행형(progressive)으로 기록해 recall 이 "실제로 맞았던 유사 상황"을 참고하게 한다.
        # 숫자(메타러너 방향/점수)엔 절대 반영 안 함 — recall 참고·표시용 사후 라벨일 뿐(불변식).
        self.episode_outcome_enabled = _env_bool("EPISODE_OUTCOME_ENABLED", default=True)
        # 다중 horizon(일). primary=20 은 프로덕션 return 채널 타깃(fwd_return_20d)과 정렬한다.
        # 5=단기(어텐션), 20=canonical, 60=장기(펀더멘털/PEAD) — 단일값 하드코딩 위험 회피.
        self.episode_outcome_horizons = _env_int_list(
            "EPISODE_OUTCOME_HORIZONS", default=[5, 20, 60]
        )
        self.episode_outcome_primary_days = int(getenv("EPISODE_OUTCOME_PRIMARY_DAYS", "20"))
        # 한 회차(태스크)당 훑는 에피소드 상한(공정 drain — 큰 백로그도 사이클을 안 막게).
        self.episode_outcome_batch_limit = int(getenv("EPISODE_OUTCOME_BATCH_LIMIT", "500"))
        # 드레인 데몬이 리코더 태스크를 재시드하는 최소 간격(초, 기본 일 1회). 열린 태스크가
        # 있거나 최근 이 간격 내 완료분이 있으면 재시드하지 않는다(중복 방지).
        self.episode_outcome_interval_sec = float(
            getenv("EPISODE_OUTCOME_INTERVAL_SEC", "86400")
        )

        # ── LLM 코호트 채점 (수식 → LLM 점수 산출 전환) ──
        # ⚠️ 이 블록은 "숫자는 결정론이 소유, LLM 은 근거만" 불변식의 **의도적 폐기**다
        # (2026-07-13 사용자 승인·팀 고지 필요). 켜면 소스 점수·방향을 LLM 코호트 채점기
        # (analyzers/llm_scorer.score_cohort)가 산출하고, 해당 소스의 기존 per-stock
        # 결정론 분석 태스크는 skip 된다. 기존 {SOURCE}_LLM_ENABLED(근거 에이전트 게이트)
        # 와는 의미가 다르므로 이름을 분리한다.
        self.llm_scoring_enabled = _env_bool("LLM_SCORING_ENABLED", default=False)
        # 점진 전환용 소스 목록(콤마). 마스터 on + 이 목록에 있는 소스만 LLM 채점.
        self.llm_scoring_sources = [
            s.strip().upper()
            for s in _env_list(
                "LLM_SCORING_SOURCES",
                default=["HIRING", "PATENT", "DATALAB", "DART", "REPORT", "PRICE"],
            )
        ]
        # LLM 호출 실패 시: "rules"=결정론 채점(reference_scorer)으로 폴백해 발행 공백 방지
        # (analysis_source="rules_fallback" 로 관측 가능). "no_signal"=그날 그 소스는 아무것도
        # 쓰지 않는다 — 집계 fan-in 의 last-known 재사용이 어제 LLM 점수를 이어받는다
        # (수식 제거(D) 후에는 이 값만 가능해진다).
        self.llm_scoring_fallback = getenv("LLM_SCORING_FALLBACK", "rules").strip().lower()
        # 통합 판정(aggregator)의 LLM 전환은 소스 검증 뒤 별도로 켠다.
        self.llm_aggregate_enabled = _env_bool("LLM_AGGREGATE_ENABLED", default=False)
        # 코호트(한 프롬프트에 함께 넣어 상대 채점하는 종목 수). 실측 비용 산정 기준 10.
        self.llm_cohort_size = int(getenv("LLM_COHORT_SIZE", "10"))
        # vertex(GCP 결제 직결·ADC) | aistudio(선불 크레딧 — 비교용).
        self.llm_scoring_provider = getenv("LLM_SCORING_PROVIDER", "vertex").strip().lower()
        self.llm_scoring_model = getenv("LLM_SCORING_MODEL", "") or getenv(
            "VERTEX_MODEL", "gemini-2.5-flash"
        )

    def llm_scoring_covers(self, source: str) -> bool:
        """이 소스의 점수 산출을 LLM 코호트 경로가 소유하는가 — per-stock 결정론 분석
        핸들러의 skip 판정과 코호트 프로듀서의 대상 소스 선정이 같은 진실을 본다."""
        return self.llm_scoring_enabled and source.upper() in self.llm_scoring_sources


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


def _env_int_list(name: str, *, default: list[int]) -> list[int]:
    value = getenv(name)
    if value is None:
        return list(default)
    out: list[int] = []
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            out.append(int(item))
        except ValueError:
            continue
    return out or list(default)
