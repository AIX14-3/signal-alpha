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

        # 단일 구독 상품가(원) + 무료 리포트 열람 횟수.
        self.subscription_price_krw = int(getenv("SUBSCRIPTION_PRICE_KRW", "9900"))
        self.subscription_plan_type = getenv("SUBSCRIPTION_PLAN_TYPE", "monthly_9900")
        self.free_report_quota = int(getenv("FREE_REPORT_QUOTA", "3"))

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
