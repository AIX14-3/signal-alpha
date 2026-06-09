"""
web_crawler.py
Selenium 기반 실제 채용공고 크롤러 (사람인)

주요 기술
  1. Anti-Bot 우회: --disable-blink-features, excludeSwitches, navigator.webdriver 은닉
  2. TimeoutException 처리: '공고 없음'은 정상 상황으로 간주
  3. URL 정규화: 절대/상대/프로토콜상대 경로 모두 처리 (이중 도메인 버그 방지)
  4. 기술 스택 추출: 직무명/요약에서 키워드 매칭 (MVP)
  5. Rate limiting: 기업 간 time.sleep

⚠️ 주의
  - 대상 사이트의 이용약관 및 robots.txt 를 반드시 준수할 것
  - 과도한 요청 금지 (Rate limit 유지)
  - 의존성: selenium, beautifulsoup4, webdriver-manager
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

try:
    from base_collector import BaseCollector          # 스크립트 실행 (main.py가 경로 추가)
except ImportError:  # pragma: no cover
    from app.collectors.hiring.base_collector import BaseCollector  # 패키지 import

logger = logging.getLogger(__name__)

_SARAMIN_BASE = "https://www.saramin.co.kr"
_SARAMIN_SEARCH = f"{_SARAMIN_BASE}/zf_user/search/recruit"

# 기술 키워드 사전 (job_title/요약에서 추출)
_TECH_KEYWORDS = [
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
    "HBM", "CUDA", "GPU", "ASIC", "FPGA", "Verilog", "VHDL",
    # 아키텍처
    "Microservices",
]


class WebCrawler(BaseCollector):
    """Selenium 으로 사람인 채용공고를 수집하는 수집기."""

    def __init__(self, database_url: str, target_companies: list[str], headless: bool = True):
        super().__init__(database_url)
        self.target_companies = target_companies
        self.headless = headless
        self.driver = None

    # ── WebDriver 초기화 ────────────────────────────────────────────────────────
    def _setup_driver(self) -> None:
        """
        Chrome WebDriver 초기화 (Anti-Bot + 안정성).

        안정성 옵션:
          --disable-gpu           헤드리스 환경의 GPU 초기화 실패 크래시 방지
          --disable-dev-shm-usage /dev/shm 메모리 부족 방지 (컨테이너/Windows 공통)
          --window-size=1920,1080 뷰포트 고정 (레이아웃 파싱 안정화)

        Anti-Bot:
          --disable-blink-features=AutomationControlled  ← 자동화 플래그 숨김
          ※ '--blink-features=AutomationControlled' 는 반대 효과이므로 사용 금지
        """
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        from selenium.webdriver.chrome.service import Service

        opts = Options()
        if self.headless:
            opts.add_argument("--headless=new")

        # ── 안정성 ────────────────────────────────────────────────────────────
        opts.add_argument("--disable-gpu")             # GPU 가속 비활성화 (헤드리스 필수)
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")   # 공유 메모리 부족 방지
        opts.add_argument("--disable-plugins")
        opts.add_argument("--disable-extensions")
        opts.add_argument("--window-size=1920,1080")   # 뷰포트 고정
        opts.add_argument("--blink-settings=imagesEnabled=false")  # 이미지 로드 차단

        # ── Anti-Bot ──────────────────────────────────────────────────────────
        opts.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        opts.add_argument("--disable-blink-features=AutomationControlled")
        opts.add_experimental_option("excludeSwitches", ["enable-automation"])
        opts.add_experimental_option("useAutomationExtension", False)

        try:
            from webdriver_manager.chrome import ChromeDriverManager
            service = Service(ChromeDriverManager().install())
            self.driver = webdriver.Chrome(service=service, options=opts)
        except Exception:
            # webdriver-manager 실패 시 PATH의 chromedriver 사용 시도
            self.driver = webdriver.Chrome(options=opts)

        # navigator.webdriver 은닉
        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
        )
        logger.info("✓ Chrome WebDriver 초기화 완료 (Anti-Bot 강화)")

    # ── 수집 ─────────────────────────────────────────────────────────────────────
    def collect(self) -> list:
        """대상 기업별 사람인 채용공고 크롤링."""
        self._setup_driver()
        all_jobs: list[dict] = []
        try:
            for company in self.target_companies:
                try:
                    logger.info("🔄 %s 크롤링 중...", company)
                    jobs = self._crawl_saramin(company)
                    all_jobs.extend(jobs)
                    logger.info("✓ %s: %d개 수집", company, len(jobs))
                    time.sleep(3)  # Rate limiting
                except Exception as exc:
                    logger.warning("⚠️  %s 크롤링 오류: %s", company, exc)
                    continue
            return all_jobs
        finally:
            if self.driver:
                self.driver.quit()
                logger.info("✓ WebDriver 종료")

    def _crawl_saramin(self, company_name: str) -> list[dict]:
        """사람인 단일 기업 검색 결과 파싱."""
        from bs4 import BeautifulSoup
        from selenium.webdriver.common.by import By
        from selenium.webdriver.support import expected_conditions as EC
        from selenium.webdriver.support.ui import WebDriverWait
        from selenium.common.exceptions import TimeoutException

        self.driver.get(f"{_SARAMIN_SEARCH}?searchword={company_name}&searchType=search")

        try:
            WebDriverWait(self.driver, 5).until(
                EC.presence_of_all_elements_located((By.CLASS_NAME, "item_recruit"))
            )
        except TimeoutException:
            logger.info("ℹ️  %s: 채용공고 없음 (정상)", company_name)
            return []

        time.sleep(1.5)
        soup = BeautifulSoup(self.driver.page_source, "html.parser")
        elements = soup.find_all("div", class_="item_recruit")
        if not elements:
            logger.info("ℹ️  %s: 채용공고 0개", company_name)
            return []

        jobs: list[dict] = []
        for elem in elements:
            try:
                link_el = elem.find("a", class_="item_title") or elem.find("h2", class_="job_tit")
                if link_el and link_el.name != "a":
                    link_el = link_el.find("a")
                if not link_el:
                    continue

                job_title = link_el.get_text(strip=True)
                full_url = self._normalize_url(link_el.get("href", ""))

                corp_el = elem.find("strong", class_="corp_name") or elem.find("span", class_="corp_name")
                corp_name = corp_el.get_text(strip=True) if corp_el else company_name

                deadline_el = elem.find("span", class_="date")
                deadline = deadline_el.get_text(strip=True) if deadline_el else "상시"

                desc_el = elem.find("div", class_="job_sector") or elem.find("span", class_="job_summary")
                description = desc_el.get_text(strip=True) if desc_el else ""

                jobs.append({
                    "company_name": corp_name,
                    "job_title": job_title,
                    "job_description": description,
                    "closing_date": deadline,
                    "source_url": full_url,
                    "job_link": full_url,
                    "posting_date": datetime.now(timezone.utc).isoformat(),
                })
            except Exception as exc:
                logger.debug("개별 파싱 실패: %s", exc)
                continue
        return jobs

    @staticmethod
    def _normalize_url(href: str) -> str:
        """상대/프로토콜상대/절대 경로를 절대 URL 로 정규화 (이중 도메인 방지)."""
        href = (href or "").strip()
        if not href:
            return _SARAMIN_BASE
        if href.startswith("http"):
            return href
        if href.startswith("//"):
            return f"https:{href}"
        if href.startswith("/"):
            return f"{_SARAMIN_BASE}{href}"
        return f"{_SARAMIN_BASE}/{href}"

    # ── 파싱 (표준 포맷 변환) ────────────────────────────────────────────────────
    def parse(self, raw_data) -> dict:
        """크롤링 원시 dict → 표준 포맷. 기술 스택은 직무명+요약에서 추출."""
        search_text = f"{raw_data['job_title']} {raw_data.get('job_description', '')}"
        return {
            "source_type":     "HIRING_POSTING_WEB",   # 논리 라벨
            "company_name":    raw_data["company_name"],
            "job_title":       raw_data["job_title"],
            "job_description": raw_data.get("job_description"),
            "closing_date":    raw_data.get("closing_date"),
            "source_url":      raw_data["source_url"],
            "job_link":        raw_data["job_link"],
            "unique_key":      None,                    # 없으면 base 가 job_link 로 해시
            "tech_stack":      self._extract_tech_keywords(search_text),
            "story":           None,
            "signal_strength": None,
            "posting_date":    raw_data.get("posting_date"),
        }

    @staticmethod
    def _extract_tech_keywords(text: str) -> list[str]:
        """텍스트에서 기술 키워드 추출 (대소문자 무시, 중복 제거)."""
        low = text.lower()
        found = [t for t in _TECH_KEYWORDS if t.lower() in low]
        return sorted(set(found))
