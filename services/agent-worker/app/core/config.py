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
        self.database_url: str = getenv(
            "DATABASE_URL", "postgresql://postgres:password@localhost:5432/signal_alpha"
        )
        self.openai_api_key: str | None = getenv("OPENAI_API_KEY")
        # parsed_reports.json 위치 (프로젝트 루트 기준)
        self.parsed_reports_path: Path = (
            Path(__file__).resolve().parents[4] / "data" / "parsed_reports.json"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
