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


@lru_cache
def get_settings() -> Settings:
    return Settings()
