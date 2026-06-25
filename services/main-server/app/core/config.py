from functools import lru_cache
from os import getenv


class Settings:
    def __init__(self) -> None:
        self.service_name = getenv("SERVICE_NAME", "main-server")
        self.version = getenv("SERVICE_VERSION", "0.1.0")
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
            for origin in getenv("CORS_ALLOW_ORIGINS", "http://localhost:3000").split(",")
            if origin.strip()
        ]

        # --- 신규 기획 ---
        # 포트원 V2 REST API. api_secret 미설정 시 dev 모드(외부 호출 없이 결정적 모의값).
        # 인증 헤더: `Authorization: PortOne {api_secret}` / 베이스: https://api.portone.io
        self.portone_api_base = getenv("PORTONE_API_BASE", "https://api.portone.io")
        self.portone_api_secret = getenv("PORTONE_API_SECRET")
        self.portone_store_id = getenv("PORTONE_STORE_ID")
        # 포트원 V2 웹훅 서명 검증 시크릿(standard-webhooks). 미설정 시 서명검증 생략(로컬/dev).
        self.portone_webhook_secret = getenv("PORTONE_WEBHOOK_SECRET")

        # 단일 구독 상품가(원) + 무료 리포트 열람 횟수.
        self.subscription_price_krw = int(getenv("SUBSCRIPTION_PRICE_KRW", "9900"))
        self.subscription_plan_type = getenv("SUBSCRIPTION_PLAN_TYPE", "monthly_9900")
        self.subscription_price_yearly_krw = int(getenv("SUBSCRIPTION_PRICE_YEARLY_KRW", "99000"))
        self.free_report_quota = int(getenv("FREE_REPORT_QUOTA", "3"))
        # 구독 만료 임박 안내 기준(일) + 청약철회(전액환불) 기간(일).
        self.subscription_expiring_soon_days = int(getenv("SUBSCRIPTION_EXPIRING_SOON_DAYS", "7"))
        self.refund_full_window_days = int(getenv("REFUND_FULL_WINDOW_DAYS", "7"))

        # 알림 메일(SMTP). smtp_host 미설정 시 dev 모드(발송 대신 로그).
        self.smtp_host = getenv("SMTP_HOST")
        self.smtp_port = int(getenv("SMTP_PORT", "587"))
        self.smtp_user = getenv("SMTP_USER")
        self.smtp_password = getenv("SMTP_PASSWORD")
        self.email_from = getenv("EMAIL_FROM", "Signal Alpha <no-reply@signal-alpha.app>")

        # 소셜 OAuth(naver/google/kakao) — provider별 client id/secret.
        self.social_providers = {
            provider: {
                "client_id": getenv(f"{provider.upper()}_CLIENT_ID"),
                "client_secret": getenv(f"{provider.upper()}_CLIENT_SECRET"),
            }
            for provider in ("naver", "google", "kakao")
        }

    @property
    def portone_dev_mode(self) -> bool:
        """V2 api_secret 미설정이면 외부 호출 없이 결정적 모의값으로 동작(로컬/CI)."""
        return not self.portone_api_secret


@lru_cache
def get_settings() -> Settings:
    return Settings()
