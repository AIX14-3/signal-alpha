"""
base_site.py
사이트별 크롤러의 공통 ABC (Strategy Pattern의 구체 전략 인터페이스)

각 사이트 크롤러가 구현해야 할 단 하나의 메서드: crawl(company_name) -> list[dict]
DB 적재 로직은 없음 — 순수 크롤링/파싱만 담당.

반환 dict 표준 키:
    company_name    str       기업명
    job_title       str       직무명
    job_description str|None  직무 설명
    closing_date    str|None  마감일 (원문 그대로)
    source_url      str       공고 URL (external_id 용)
    job_link        str       source_url 과 동일
    posting_date    str       수집 시각 ISO (UTC)
    tech_stack      list[str] 기술 키워드 (추출 불가시 [])
    story           None      (공식 사이트는 signal 스토리 없음)
    signal_strength None
    source_type     str       논리 라벨 (예: 'SARAMIN', 'JOBKOREA', 'SAMSUNG_CAREERS' ...)
    unique_key      str|None  source_hash 시드 (없으면 base_collector 가 job_link 로 대체)
"""

from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# 기술 키워드 추출용 공통 사전
TECH_KEYWORDS: list[str] = [
    # 언어
    "Python", "Java", "JavaScript", "TypeScript", "C++", "C#", "Go", "Rust",
    "Kotlin", "Swift", "Scala",
    # 프론트엔드
    "React", "Vue", "Angular", "Svelte", "Next.js",
    # 백엔드
    "Django", "Flask", "FastAPI", "Spring", "Express", "Node.js", "Rails", "Laravel",
    # 클라우드/인프라
    "AWS", "Azure", "GCP", "Kubernetes", "Docker", "Terraform", "Jenkins",
    # 데이터
    "PostgreSQL", "MySQL", "MongoDB", "Redis", "Elasticsearch", "Cassandra",
    # AI/ML
    "AI", "ML", "Machine Learning", "Deep Learning", "TensorFlow", "PyTorch",
    "NLP", "Computer Vision", "LLM", "Transformers",
    # 반도체/HW
    "HBM", "CUDA", "GPU", "ASIC", "FPGA", "Verilog", "VHDL", "AUTOSAR",
    # 아키텍처
    "Microservices", "gRPC", "Kafka",
]


class BaseSiteCrawler(ABC):
    """사이트 크롤러 공통 인터페이스."""

    source_label: str = "UNKNOWN"   # 하위 클래스에서 반드시 override

    def __init__(self, driver=None):
        """
        Args:
            driver: 공유 Selenium WebDriver. None 이면 필요 시 스스로 생성.
        """
        self.driver = driver

    @abstractmethod
    def crawl(self, company_name: str) -> list[dict]:
        """
        해당 사이트에서 company_name 의 채용공고를 수집해 표준 dict 리스트로 반환.
        예외 발생 시 호출부(MultiSourceCrawler)가 로깅 후 빈 리스트로 처리.
        """

    # ── 공통 유틸 ──────────────────────────────────────────────────────────────
    @staticmethod
    def now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def extract_tech(text: str) -> list[str]:
        """텍스트에서 기술 키워드 추출 (대소문자 무시, 중복 제거, 정렬)."""
        low = text.lower()
        return sorted({t for t in TECH_KEYWORDS if t.lower() in low})

    @staticmethod
    def normalize_url(href: str, base: str) -> str:
        """상대/프로토콜상대 경로를 절대 URL 로 변환."""
        href = (href or "").strip()
        if not href:
            return base
        if href.startswith("http"):
            return href
        if href.startswith("//"):
            return f"https:{href}"
        if href.startswith("/"):
            proto = base.split("//")[0]
            domain = base.split("//")[1].split("/")[0]
            return f"{proto}//{domain}{href}"
        return f"{base.rstrip('/')}/{href}"

    def _make_record(
        self,
        company_name: str,
        job_title: str,
        source_url: str,
        *,
        job_description: str | None = None,
        closing_date: str | None = None,
        tech_stack: list[str] | None = None,
    ) -> dict:
        """표준 레코드 dict 생성 헬퍼."""
        return {
            "source_type":     self.source_label,
            "company_name":    company_name,
            "job_title":       job_title,
            "job_description": job_description,
            "closing_date":    closing_date,
            "source_url":      source_url,
            "job_link":        source_url,
            "unique_key":      None,
            "tech_stack":      tech_stack or self.extract_tech(
                f"{job_title} {job_description or ''}"
            ),
            "story":           None,
            "signal_strength": None,
            "posting_date":    self.now_iso(),
        }

    # ── Selenium 편의 메서드 ────────────────────────────────────────────────────
    def _wait_for(self, by, value, timeout: int = 8):
        """요소 대기 (Selenium 없으면 ImportError)."""
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.webdriver.support import expected_conditions as EC
        return WebDriverWait(self.driver, timeout).until(
            EC.presence_of_element_located((by, value))
        )

    def _safe_get(self, url: str, wait_sec: float = 1.5) -> None:
        """페이지 이동 후 대기."""
        self.driver.get(url)
        time.sleep(wait_sec)
