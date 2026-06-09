"""
mock_collector.py
Fixture 기반 채용 수집기 (테스트·프로토타입용)

장점
  - 매우 빠름 (네트워크 없음)
  - 항상 동일한 데이터 → 재현 가능
  - 개발/CI 환경에서 안전

데이터 출처
  database/seeds/alternative_raw_fixture.json
  (생성: python database/seeds/alternative_raw_fixture.py)
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

try:
    from base_collector import BaseCollector          # 스크립트 실행 (main.py가 경로 추가)
except ImportError:  # pragma: no cover
    from app.collectors.hiring.base_collector import BaseCollector  # 패키지 import

logger = logging.getLogger(__name__)

# 기본 Fixture 경로: signal-alpha/database/seeds/alternative_raw_fixture.json
#   parents[0]=hiring [1]=collectors [2]=app [3]=agent-worker [4]=services [5]=signal-alpha
_DEFAULT_FIXTURE = (
    Path(__file__).resolve().parents[5] / "database" / "seeds" / "alternative_raw_fixture.json"
)


class MockCollector(BaseCollector):
    """Fixture JSON 에서 채용 데이터를 읽는 수집기."""

    def __init__(self, database_url: str, fixture_path: str | Path | None = None):
        super().__init__(database_url)
        self.fixture_path = Path(fixture_path) if fixture_path else _DEFAULT_FIXTURE

    def collect(self) -> list:
        """Fixture JSON 로드 → seed_job_data 리스트 반환."""
        if not self.fixture_path.exists():
            raise FileNotFoundError(
                f"Fixture 파일 없음: {self.fixture_path}\n"
                "먼저 실행하세요 → python database/seeds/alternative_raw_fixture.py"
            )
        with open(self.fixture_path, encoding="utf-8") as f:
            data = json.load(f)
        logger.info("✓ Fixture 로드 완료: %s", self.fixture_path)
        return data["seed_job_data"]

    def parse(self, raw_data) -> dict:
        """Fixture 레코드 → 표준 포맷. (이미 정제된 데이터이므로 키 매핑만 수행)"""
        return {
            "source_type":     raw_data.get("source_type", "SEED_JOB"),  # 논리 라벨
            "company_name":    raw_data["company_name"],
            "job_title":       raw_data["job_title"],
            "job_description": raw_data.get("job_description"),
            "closing_date":    raw_data.get("closing_date"),
            "source_url":      raw_data.get("job_link"),
            "job_link":        raw_data.get("job_link"),
            "unique_key":      raw_data.get("unique_key"),   # source_hash seed
            "tech_stack":      raw_data.get("tech_stack", []),
            "story":           raw_data.get("story"),
            "signal_strength": raw_data.get("signal_strength"),
            "posting_date":    raw_data.get("posting_date"),
        }
