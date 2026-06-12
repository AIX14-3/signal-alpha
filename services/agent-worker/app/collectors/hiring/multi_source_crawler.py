"""
multi_source_crawler.py
멀티소스 채용공고 수집기 (Strategy Pattern 오케스트레이터)

수집 소스 (총 5계층):
  1. 사람인 (Saramin)             — 키워드 검색 기반 (모든 is_target 기업)
  2. 잡코리아 (Jobkorea)          — 키워드 검색 기반 (모든 is_target 기업)
  3. official_api                  — 공식 API (driver=None, 삼성전자 등)
  4. official_selenium             — 공식 SPA/ATS (NAVER, 카카오, SK하이닉스 등)
  5. recruiter_kr / simple_site    — 집계/정적 사이트

크롤러 설정은 hiring_sources 테이블에서 동적 로드:
  기업 추가/변경 = hiring_sources INSERT/UPDATE 한 줄 (코드 수정 불필요)

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
    from base_collector import BaseCollector
except ImportError:
    from app.collectors.hiring.base_collector import BaseCollector

logger = logging.getLogger(__name__)


def _load_crawler_registry() -> dict[str, type]:
    """사용 가능한 크롤러 클래스 레지스트리 반환. 클래스 등록은 코드, 매핑 데이터는 DB."""
    try:
        from sites.company import (
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

    return {
        "SamsungCrawler":    SamsungCrawler,
        "NaverCrawler":      NaverCrawler,
        "KakaoCrawler":      KakaoCrawler,
        "SKHynixCrawler":    SKHynixCrawler,
        "KraftonCrawler":    KraftonCrawler,
        "HybeCrawler":       HybeCrawler,
        "SMCrawler":         SMCrawler,
        "HyundaiCrawler":    HyundaiCrawler,
        "KiaCrawler":        KiaCrawler,
        "SimpleSiteCrawler": SimpleSiteCrawler,
        "RecruiterKrCrawler": RecruiterKrCrawler,
    }


def _build_crawler_map(db_url: str, driver) -> dict[str, object]:
    """
    hiring_sources + stocks 조인 → {company_name: CrawlerInstance}

    crawler_type = 'official_api' → driver=None (requests 전용)
    그 외 모든 타입                → driver 전달 (Selenium 필요)
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url, echo=False, future=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT s.name, hs.crawler_type, hs.crawler_class
                FROM hiring_sources hs
                JOIN stocks s ON s.id = hs.stock_id
                WHERE hs.is_active = TRUE
                  AND s.is_target = TRUE
                ORDER BY s.name
            """)).fetchall()
    finally:
        engine.dispose()

    registry = _load_crawler_registry()
    crawlers: dict[str, object] = {}

    for row in rows:
        company_name    = row.name
        crawler_cls_name = row.crawler_class
        use_driver = None if row.crawler_type == "official_api" else driver

        cls = registry.get(crawler_cls_name)
        if cls is None:
            logger.warning("⚠️  미등록 크롤러 클래스: %s (%s) — 스킵", crawler_cls_name, company_name)
            continue

        crawlers[company_name] = cls(driver=use_driver)
        logger.debug("  크롤러 등록: %s → %s (driver=%s)", company_name, crawler_cls_name,
                     "None" if use_driver is None else "WebDriver")

    return crawlers


class MultiSourceCrawler(BaseCollector):
    """
    모든 소스를 통합 수집하는 오케스트레이터.

    수집 대상 기업 목록은 BaseCollector.run()이 DB(is_target=TRUE)에서 동적으로 조회해
    collect(target_companies)로 주입한다.

    공식 사이트 크롤러 매핑은 hiring_sources 테이블에서 로드한다.
    기업 추가/변경은 SQL INSERT/UPDATE 한 줄로 처리 (코드 재배포 불필요).

    Args:
        database_url:         PostgreSQL 연결 문자열
        headless:             Selenium 헤드리스 모드 (기본 True)
        use_portals:          사람인/잡코리아 포털 수집 여부 (기본 True)
        use_official:         기업 공식 사이트 수집 여부 (기본 True)
        rate_limit_sec:       기업 간 대기 시간 (기본 2.0s)
        driver_rotation_size: 몇 개 기업마다 Chrome 을 재시작할지 (기본 3).
                              헤드리스 Chrome 의 메모리 누적 크래시 방지.
                              0 또는 음수면 로테이션 비활성화.
    """

    def __init__(
        self,
        database_url: str,
        headless: bool = True,
        use_portals: bool = True,
        use_official: bool = True,
        rate_limit_sec: float = 2.0,
        driver_rotation_size: int = 3,
    ):
        super().__init__(database_url)
        self.headless = headless
        self.use_portals = use_portals
        self.use_official = use_official
        self.rate_limit_sec = rate_limit_sec
        self.driver_rotation_size = max(driver_rotation_size, 0)
        self.driver = None

    # ── WebDriver 초기화 ──────────────────────────────────────────────────────
    def _setup_driver(self) -> None:
        """Chrome WebDriver 초기화. driver_utils.create_chrome_driver 에 위임."""
        try:
            from driver_utils import create_chrome_driver
        except ImportError:
            from app.collectors.hiring.driver_utils import create_chrome_driver
        self.driver = create_chrome_driver(self.headless)

    def _quit_driver(self) -> None:
        """
        드라이버 안전 종료 헬퍼.
        이미 죽은 세션이거나 None 이면 조용히 무시 (중복 quit 방지).
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
    def collect(self, target_companies: list[str]) -> list:
        """
        전 소스에서 채용 데이터 수집 후 하나의 list[dict] 로 반환.

        드라이버 로테이션:
          driver_rotation_size(기본 3) 기업마다 Chrome 을 재시작.
          official_crawlers 는 로테이션 시 driver 참조를 갱신하기 위해 루프마다 재생성.
        """
        self._setup_driver()
        all_jobs: list[dict] = []

        # DB에서 공식 사이트 크롤러 맵 로드
        official_crawlers: dict[str, object] = {}
        if self.use_official:
            try:
                official_crawlers = _build_crawler_map(self.database_url, self.driver)
                logger.info("✓ hiring_sources 로드: %d개 기업 공식 크롤러", len(official_crawlers))
            except Exception as exc:
                logger.warning("⚠️  hiring_sources 로드 실패 (공식 사이트 스킵): %s", exc)

        try:
            for idx, company in enumerate(target_companies):

                # ── 드라이버 로테이션 ────────────────────────────────────────
                if (
                    self.driver_rotation_size > 0
                    and idx > 0
                    and idx % self.driver_rotation_size == 0
                ):
                    logger.info(
                        "🔄 드라이버 로테이션 (기업 %d/%d, 배치 크기 %d) — 재시작 중...",
                        idx + 1, len(target_companies), self.driver_rotation_size,
                    )
                    self._quit_driver()
                    self._setup_driver()
                    # 드라이버 교체 후 공식 크롤러 맵 재빌드 (새 driver 참조 반영)
                    if self.use_official:
                        try:
                            official_crawlers = _build_crawler_map(
                                self.database_url, self.driver
                            )
                        except Exception as exc:
                            logger.warning("⚠️  크롤러 맵 재빌드 실패: %s", exc)

                company_jobs: list[dict] = []
                logger.info("─" * 60)
                logger.info("🏢 [%d/%d] %s 수집 시작",
                            idx + 1, len(target_companies), company)

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

                # 2) 공식 사이트 수집 (hiring_sources DB 기반)
                if self.use_official:
                    official_crawler = official_crawlers.get(company)
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
            self._quit_driver()
            logger.info("✓ WebDriver 최종 종료")

        logger.info("=" * 60)
        logger.info("📊 전체 수집 합계: %d건 (중복 포함)", len(all_jobs))
        return all_jobs

    # ── 파싱 (이미 표준 포맷 — 그대로 통과) ─────────────────────────────────
    def parse(self, raw_data) -> dict:
        """BaseSiteCrawler._make_record() 이 이미 표준 포맷을 반환하므로 누락 키만 보완."""
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
