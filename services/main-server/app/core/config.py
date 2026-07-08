from functools import lru_cache
from os import getenv


class Settings:
    def __init__(self) -> None:
        self.service_name = getenv("SERVICE_NAME", "main-server")
        self.version = getenv("SERVICE_VERSION", "0.1.0")
        # 배포 환경. production 일 때만 아래 _validate_production 가드가 켜진다(로컬/CI=development).
        self.app_env = getenv("APP_ENV", "development").lower()
        self.database_url = getenv("DATABASE_URL")
        self.auth_secret_key = getenv("AUTH_SECRET_KEY", "dev-main-server-secret-change-me")
        self.access_token_expire_minutes = int(getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "30"))
        self.refresh_token_expire_days = int(getenv("REFRESH_TOKEN_EXPIRE_DAYS", "14"))

        # refresh 토큰의 슬라이딩 연장 절대 상한(일). 발급(로그인) 후 이 기간을 넘기면
        # 활동 중이어도 재로그인 강제 → 무한 세션 방지.
        self.refresh_absolute_max_days = int(getenv("REFRESH_ABSOLUTE_MAX_DAYS", "30"))

        # refresh 토큰은 HttpOnly 쿠키로 전달(XSS 토큰 탈취 차단). path 스코프로
        # /api/auth(refresh·logout) 에만 자동 송신된다. 로컬(http)은 COOKIE_SECURE=false 필요.
        self.cookie_secure = getenv("COOKIE_SECURE", "false").lower() in ("1", "true", "yes")
        self.cookie_samesite = getenv("COOKIE_SAMESITE", "lax").lower()
        self.cookie_domain = getenv("COOKIE_DOMAIN") or None
        self.refresh_cookie_name = getenv("REFRESH_COOKIE_NAME", "sa_refresh")
        self.refresh_cookie_path = getenv("REFRESH_COOKIE_PATH", "/api/auth")

        # 관리자 세션도 동일하게 HttpOnly 쿠키로 관리(/api/admin 스코프).
        self.admin_cookie_name = getenv("ADMIN_COOKIE_NAME", "sa_admin")
        self.admin_cookie_path = getenv("ADMIN_COOKIE_PATH", "/api/admin")
        # 브라우저 프론트(web)에서의 CORS 허용 오리진(쉼표 구분). 로컬 기본값=Next dev.
        self.cors_allow_origins = [
            origin.strip()
            for origin in getenv(
                "CORS_ALLOW_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
            ).split(",")
            if origin.strip()
        ]

        # --- 신규 기획 ---
        # 포트원 V2 REST API. **항상 real 모드**(과거 dev 폴백 제거 — app/core/portone.py 참조).
        # api_secret 미설정 시 결제/본인인증 검증은 조용한 모의값이 아니라 명확한 오류로 실패한다.
        # 인증 헤더: `Authorization: PortOne {api_secret}` / 베이스: https://api.portone.io
        self.portone_api_base = getenv("PORTONE_API_BASE", "https://api.portone.io")
        self.portone_api_secret = getenv("PORTONE_API_SECRET")
        self.portone_store_id = getenv("PORTONE_STORE_ID")
        # 포트원 V2 웹훅 서명 검증 시크릿(standard-webhooks). 미설정 시 서명검증 생략(로컬/dev).
        self.portone_webhook_secret = getenv("PORTONE_WEBHOOK_SECRET")

        # 단일 구독 상품가(원).
        self.subscription_price_krw = int(getenv("SUBSCRIPTION_PRICE_KRW", "9900"))
        self.subscription_plan_type = getenv("SUBSCRIPTION_PLAN_TYPE", "monthly_9900")
        self.subscription_price_yearly_krw = int(getenv("SUBSCRIPTION_PRICE_YEARLY_KRW", "99000"))
        # 구독 만료 임박 안내 기준(일) + 청약철회(전액환불) 기간(일).
        self.subscription_expiring_soon_days = int(getenv("SUBSCRIPTION_EXPIRING_SOON_DAYS", "7"))
        self.refund_full_window_days = int(getenv("REFUND_FULL_WINDOW_DAYS", "7"))

        # 알림 메일(SMTP). smtp_host 미설정 시 dev 모드(발송 대신 로그).
        self.smtp_host = getenv("SMTP_HOST")
        self.smtp_port = int(getenv("SMTP_PORT", "587"))
        self.smtp_user = getenv("SMTP_USER")
        self.smtp_password = getenv("SMTP_PASSWORD")
        self.email_from = getenv("EMAIL_FROM", "Signal Alpha <no-reply@signal-alpha.app>")

        # Admin 운영 패널은 main-server 경유로 agent-worker 내부 운영 API를 호출한다.
        self.agent_worker_internal_base_url = getenv(
            "AGENT_WORKER_INTERNAL_URL",
            getenv("WORKER_INTERNAL_URL", "http://localhost:8011"),
        )
        self.internal_api_token = getenv("INTERNAL_API_TOKEN", "")

        # 소셜 OAuth(naver/google/kakao) — provider별 client id/secret.
        self.social_providers = {
            provider: {
                "client_id": getenv(f"{provider.upper()}_CLIENT_ID"),
                "client_secret": getenv(f"{provider.upper()}_CLIENT_SECRET"),
            }
            for provider in ("naver", "google", "kakao")
        }

        # --- 보안 강화(Spring Security 대응) ---
        # Host 헤더 허용목록(TrustedHost). 콤마구분. 기본=로컬/테스트. prod 는 실도메인 명시.
        self.allowed_hosts = [
            host.strip()
            for host in getenv("ALLOWED_HOSTS", "localhost,127.0.0.1,testserver").split(",")
            if host.strip()
        ]
        # 응답 보안 헤더(nosniff/X-Frame/Referrer/Permissions/CSP). HSTS 는 HTTPS(prod)에서만.
        self.security_headers_enabled = getenv("SECURITY_HEADERS_ENABLED", "true").lower() in (
            "1", "true", "yes"
        )
        self.hsts_enabled = self.app_env == "production"
        # 신뢰 프록시 홉 수. rate limit·관리자 잠금이 클라이언트 IP 를 정할 때, X-Forwarded-For
        # 좌측(클라이언트가 위조 가능)을 신뢰하지 않고 **우측에서 이 홉 수만큼 벗겨낸** 실클라이언트를
        # 쓴다. 기본 0 = XFF 완전 무시(소켓 peer) → 헤더 위조로 rate limit/잠금을 우회할 수 없다.
        # GKE(GCE ingress 가 XFF 를 재작성/추가)에선 프록시 홉 수(보통 1)로 설정한다.
        self.trusted_proxy_count = int(getenv("TRUSTED_PROXY_COUNT", "0"))
        # 브루트포스 방어: 인증 엔드포인트 IP 레이트리밋 + 관리자 로그인 연속실패 잠금.
        self.rate_limit_enabled = getenv("RATE_LIMIT_ENABLED", "true").lower() in ("1", "true", "yes")
        self.rate_limit_auth_max = int(getenv("RATE_LIMIT_AUTH_MAX", "60"))
        self.rate_limit_auth_window_seconds = int(getenv("RATE_LIMIT_AUTH_WINDOW_SECONDS", "300"))
        self.admin_login_max_failures = int(getenv("ADMIN_LOGIN_MAX_FAILURES", "10"))
        self.admin_lockout_seconds = int(getenv("ADMIN_LOCKOUT_SECONDS", "900"))

        # 프로덕션 배포 안전장치(G5/G6). APP_ENV=production 일 때만 검증 — 잘못 배포되면
        # 부팅 시점에 즉시 실패(토큰 위조·CORS·쿠키 오설정으로 조용히 깨지는 것 방지).
        self._validate_production()

    def _validate_production(self) -> None:
        if self.app_env != "production":
            return
        problems: list[str] = []
        # G5: dev 기본 시크릿으로 프로덕션 토큰 서명 금지(위조 위험).
        if not self.auth_secret_key or self.auth_secret_key.startswith("dev-"):
            problems.append("AUTH_SECRET_KEY must be a strong, non-default secret")
        # G6: credentials=True 인 CORS 는 와일드카드 불가 + 명시 오리진 필수.
        if not self.cors_allow_origins:
            problems.append("CORS_ALLOW_ORIGINS must list the frontend origin(s)")
        if "*" in self.cors_allow_origins:
            problems.append("CORS_ALLOW_ORIGINS must not contain '*' (invalid with credentials)")
        # G7: Host 헤더 위조(host header injection) 방지 — prod 는 실도메인을 명시해야 한다.
        if not self.allowed_hosts or "*" in self.allowed_hosts:
            problems.append("ALLOWED_HOSTS must list explicit hostnames (no '*') in production")
        # G6: 교차도메인 쿠키(SameSite=None)는 Secure 가 없으면 브라우저가 거부.
        if self.cookie_samesite == "none" and not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true when COOKIE_SAMESITE=none")
        # G8: HttpOnly refresh/admin 쿠키는 프로덕션(HTTPS)에서 항상 Secure 여야 한다(평문 구간 유출 방지).
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE must be true in production")
        # G9: 포트원 웹훅 서명 시크릿 필수 — 미설정 시 무서명 웹훅을 수용해 위조 결제 이벤트로
        #     구독이 부여될 수 있다(payments.webhook 의 fail-closed 와 짝).
        if not self.portone_webhook_secret:
            problems.append("PORTONE_WEBHOOK_SECRET must be set in production")
        if problems:
            raise ValueError(
                "Invalid production configuration (APP_ENV=production): " + "; ".join(problems)
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()
