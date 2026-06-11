"""
multi_source_crawler.py
멀티소스 채용공고 수집기 (Strategy Pattern 오케스트레이터)

수집 소스 (총 5계층):
  1. 사람인 (Saramin)             — 키워드 검색 기반
  2. 잡코리아 (Jobkorea)          — 키워드 검색 기반
  3. recruiter.co.kr              — HL만도, 셀트리온, 유한양행
  4. API 기반 공식 사이트          — 삼성전자, NAVER, 카카오
  5. Selenium SPA/ATS 공식 사이트  — SK하이닉스, 크래프톤, HYBE, SM엔터테인먼트
                                     한미반도체, 스튜디오드래곤, 삼성바이오로직스
                                     현대자동차, 기아

흐름:
  1. Selenium WebDriver 공유 (driver_rotation_size 기업마다 교체)
  2. 각 BaseSiteCrawler.crawl(company_name) → list[dict]
  3. 전체 결과를 BaseCollector.insert_to_db() 로 한꺼번에 적재
  4. source_hash 기반 중복 제거 (동일 공고가 여러 소스에서 수집돼도 1건만 적재)

드라이버 로테이션 전략:
  - 헤드리스 Chrome 은 45+ 페이지 이동 후 메모리 1~2 GB 누적 → 크래시
  - driver_rotation_size(기본 3) 기업마다 quit → 새 드라이버 기동
  - _quit_driver() 헬퍼로 quit 을 단일화, finally 중복 호출 방지
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

try:
    from base_collector import BaseCollector          # 스크립트 직접 실행
except ImportError:
    from app.collectors.hiring.base_collector import BaseCollector  # 패키지 import

logger = logging.getLogger(__name__)

# ── 크롤링 대상 기업 → 공식 사이트 크롤러 매핑 ──────────────────────────────
# recruiter.co.kr 기업 (RecruiterKrCrawler 가 회사별 URL 로 분기)
_RECRUITER_KR_COMPANIES = {"HL만도", "셀트리온", "유한양행"}

# API 기반 (Selenium 불필요 — driver=None 전달)
_API_COMPANIES = {"삼성전자", "NAVER", "카카오"}

# Selenium SPA/ATS (driver 공유)
_SELENIUM_COMPANIES = {
    "SK하이닉스", "크래프톤",
    "HYBE", "SM엔터테인먼트",
    "한미반도체", "스튜디오드래곤", "삼성바이오로직스",
    "현대자동차", "기아",
}


def _make_crawler(company: str, driver):
    """기업명 → 적절한 BaseSiteCrawler 인스턴스 반환."""
    try:
        from sites.company import (   # 스크립트 실행 경로 (sys.path에 collectors/ 추가됨)
            SamsungCrawler, NaverCrawler, KakaoCrawler,
            SKHynixCrawler, KraftonCrawler,
            HybeCrawler, SMCrawler,
            HyundaiCrawler, KiaCrawler,
            SimpleSiteCrawler,
        )
        from sites import RecruiterKrCrawler
    except ImportError:
        from app.collectors.hiring.sites.company import (
            SamsungCrawler, NaverCrawler, KakaoCrawler,
            SKHynixCrawler, KraftonCrawler,
            HybeCrawler, SMCrawler,
            HyundaiCrawler, KiaCrawler,
            SimpleSiteCrawler,
        )
        from app.collectors.hiring.sites import RecruiterKrCrawler

    mapping = {
        "삼성전자":         SamsungCrawler(driver=None),
        "NAVER":            NaverCrawler(driver=driver),
        "카카오":           KakaoCrawler(driver=driver),
        "SK하이닉스":       SKHynixCrawler(driver=driver),
        "크래프톤":         KraftonCrawler(driver=driver),
        "HYBE":             HybeCrawler(driver=driver),
        "SM엔터테인먼트":   SMCrawler(driver=driver),
        "현대자동차":       HyundaiCrawler(driver=driver),
        "기아":             KiaCrawler(driver=driver),
        "HL만도":           RecruiterKrCrawler(driver=driver),
        "셀트리온":         RecruiterKrCrawler(driver=driver),
        "유한양행":         RecruiterKrCrawler(driver=driver),
        "한미반도체":       SimpleSiteCrawler(driver=driver),
        "스튜디오드래곤":   SimpleSiteCrawler(driver=driver),
        "삼성바이오로직스": SimpleSiteCrawler(driver=driver),
    }
    return mapping.get(company)


class MultiSourceCrawler(BaseCollector):
    """
    모든 소스를 통합 수집하는 오케스트레이터.

    Args:
        database_url:      PostgreSQL 연결 문자열
        target_companies:  수집 대상 기업명 리스트 (None → 기본 15개)
        headless:          Selenium 헤드리스 모드 (기본 True)
        use_portals:       사람인/잡코리아 포털 수집 여부 (기본 True)
        use_official:      기업 공식 사이트 수집 여부 (기본 True)
        rate_limit_sec:    기업 간 대기 시간 (기본 2.0s)
    """

    DEFAULT_COMPANIES = [
        "삼성전자", "SK하이닉스", "한미반도체",
        "NAVER", "카카오", "크래프톤",
        "현대자동차", "기아", "HL만도",
        "HYBE", "SM엔터테인먼트", "스튜디오드래곤",
        "삼성바이오로직스", "셀트리온", "유한양행",
    ]

    def __init__(
        self,
        database_url: str,
        target_companies: list[str] | None = None,
        headless: bool = True,
        use_portals: bool = True,
        use_official: bool = True,
        rate_limit_sec: float = 2.0,
        driver_rotation_size: int = 3,
    ):
        """
        Args:
            driver_rotation_size: 몇 개 기업마다 Chrome 을 재시작할지 (기본 3).
                                   헤드리스 Chrome 의 메모리 누적 크래시 방지.
                                   0 또는 음수면 로테이션 비활성화.
        """
        super().__init__(database_url)
        self.target_companies = target_companies or self.DEFAULT_COMPANIES
        self.headless = headless
        self.use_portals = use_portals
        self.use_official = use_official
        self.rate_limit_sec = rate_limit_sec
        self.driver_rotation_size = driver_rotation_size if driver_rotation_size > 0 else 0
        self.driver = None

    # ── WebDriver 초기화 (Anti-Bot + 안정성) ─────────────────────────────────
    def _setup_driver(self) -> None:
        """
        Chrome WebDriver 초기화.

        추가된 안정성 옵션:
          --disable-gpu           헤드리스 환경의 GPU 초기화 실패 크래시 방지
          --disable-dev-shm-usage /dev/shm 메모리 부족 방지 (컨테이너/Windows 공통)
          --window-size=1920,1080 뷰포트 고정 (레이아웃 파싱 안정화)

        Anti-Bot:
          --disable-blink-features=AutomationControlled  ← 자동화 플래그 숨김
          ※ '--blink-features=AutomationControlled' 는 반대 효과 (활성화)이므로 사용 금지
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
        opts.add_argument("--blink-settings=imagesEnabled=false")  # 이미지 차단

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
            self.driver = webdriver.Chrome(options=opts)

        self.driver.execute_cdp_cmd(
            "Page.addScriptToEvaluateOnNewDocument",
            {"source": "Object.defineProperty(navigator,'webdriver',{get:()=>undefined})"},
        )
        logger.info("✓ Chrome WebDriver 초기화 완료")

    def _quit_driver(self) -> None:
        """
        드라이버 안전 종료 헬퍼.
        - 이미 죽은 세션이거나 None 이면 조용히 무시 (중복 quit 방지).
        - 로테이션과 finally 양쪽에서 이 메서드를 호출하면 중복 예외가 없음.
        """
        if self.driver is None:
            return
        try:
            self.driver.quit()
        except Exception as exc:
            logger.debug("driver.quit() 예외 (무시): %s", exc)
        finally:
            self.driver = None

    # ── BaseSiteCrawler import ────────────────────────────────────────────────
    def _get_portal_crawlers(self):
        try:
            from sites import SaraminCrawler, JobkoreaCrawler
        except ImportError:
            from app.collectors.hiring.sites import SaraminCrawler, JobkoreaCrawler
        return SaraminCrawler(driver=self.driver), JobkoreaCrawler(driver=self.driver)

    # ── 수집 ─────────────────────────────────────────────────────────────────
    def collect(self) -> list:
        """
        전 소스에서 채용 데이터 수집 후 하나의 list[dict] 로 반환.

        드라이버 로테이션:
          driver_rotation_size(기본 3) 기업마다 Chrome 을 재시작.
          각 크롤러 인스턴스는 루프 안에서 매번 생성되므로
          self.driver 를 교체하면 곧바로 새 드라이버를 사용.
        """
        # 첫 드라이버 기동
        self._setup_driver()
        all_jobs: list[dict] = []

        try:
            for idx, company in enumerate(self.target_companies):

                # ── 드라이버 로테이션 ────────────────────────────────────────
                # idx==0 은 이미 위에서 기동했으므로 건너뜀
                if (
                    self.driver_rotation_size > 0
                    and idx > 0
                    and idx % self.driver_rotation_size == 0
                ):
                    logger.info(
                        "🔄 드라이버 로테이션 (기업 %d/%d, 배치 크기 %d) — 재시작 중...",
                        idx + 1, len(self.target_companies), self.driver_rotation_size,
                    )
                    self._quit_driver()    # 기존 Chrome 완전 종료
                    self._setup_driver()   # 새 Chrome 기동 (메모리 초기화)

                company_jobs: list[dict] = []
                logger.info("─" * 60)
                logger.info("🏢 [%d/%d] %s 수집 시작",
                            idx + 1, len(self.target_companies), company)

                # 1) 포털 수집 (사람인 + 잡코리아)
                if self.use_portals:
                    saramin, jobkorea = self._get_portal_crawlers()
                    for crawler in (saramin, jobkorea):
                        try:
                            results = crawler.crawl(company)
                            company_jobs.extend(results)
                            logger.info("  ✓ [%s] %d건", crawler.source_label, len(results))
                        except Exception as exc:
                            logger.warning("  ⚠️  [%s] 오류: %s", crawler.source_label, exc)
                        time.sleep(self.rate_limit_sec)

                # 2) 공식 사이트 수집
                if self.use_official:
                    official_crawler = _make_crawler(company, self.driver)
                    if official_crawler:
                        try:
                            results = official_crawler.crawl(company)
                            company_jobs.extend(results)
                            logger.info(
                                "  ✓ [%s] %d건", official_crawler.source_label, len(results)
                            )
                        except Exception as exc:
                            logger.warning(
                                "  ⚠️  [%s] 공식 사이트 오류: %s",
                                official_crawler.source_label, exc,
                            )
                        time.sleep(self.rate_limit_sec)

                logger.info("  → %s 소계: %d건", company, len(company_jobs))
                all_jobs.extend(company_jobs)

        finally:
            # 마지막 드라이버 정리 (_quit_driver 가 None 체크하므로 중복 호출 안전)
            self._quit_driver()
            logger.info("✓ WebDriver 최종 종료")

        logger.info("=" * 60)
        logger.info("📊 전체 수집 합계: %d건 (중복 포함)", len(all_jobs))
        return all_jobs

    # ── 파싱 (이미 표준 포맷 — 그대로 통과) ─────────────────────────────────
    def parse(self, raw_data) -> dict:
        """
        BaseSiteCrawler._make_record() 이 이미 표준 포맷을 반환하므로
        필수 키가 없는 경우만 보완.
        """
        return {
            "source_type":     raw_data.get("source_type", "MULTI_SOURCE_WEB"),
            "company_name":    raw_data.get("company_name", ""),
            "job_title":       raw_data.get("job_title", "(제목 없음)"),
            "job_description": raw_data.get("job_description"),
            "closing_date":    raw_data.get("closing_date"),
            "source_url":      raw_data.get("source_url") or raw_data.get("job_link", ""),
            "job_link":        raw_data.get("job_link") or raw_data.get("source_url", ""),
            "unique_key":      raw_data.get("unique_key"),
            "tech_stack":      raw_data.get("tech_stack") or [],
            "story":           raw_data.get("story"),
            "signal_strength": raw_data.get("signal_strength"),
            "posting_date":    raw_data.get("posting_date") or datetime.now(timezone.utc).isoformat(),
        }
