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
import zoneinfo
from datetime import datetime
from typing import NamedTuple

_KST = zoneinfo.ZoneInfo("Asia/Seoul")

try:
    from base_collector import BaseCollector, CollectorResult
except ImportError:
    from app.collectors.hiring.base_collector import BaseCollector, CollectorResult

logger = logging.getLogger(__name__)


class CrawlerSpec(NamedTuple):
    """hiring_sources 한 행 = 크롤러 사양. dict 매직스트링 대신 속성 접근으로 타입 안전화."""

    company_name: str
    crawler_type: str
    crawler_class: str


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


def _load_source_specs(db_url: str) -> list[CrawlerSpec]:
    """hiring_sources + stocks 조인 → 크롤러 사양(spec) 목록.

    DB 조회는 driver 와 무관(매핑 데이터는 불변)하므로, 드라이버 로테이션 때마다
    다시 조회하지 않도록 인스턴스화(_instantiate_crawlers)와 분리한다.
    collect()는 루프 진입 전 1회만 이 함수를 호출한다.
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

    return [
        CrawlerSpec(
            company_name=row.name,
            crawler_type=row.crawler_type,
            crawler_class=row.crawler_class,
        )
        for row in rows
    ]


def _load_jobkorea_company_ids(db_url: str) -> dict[str, str]:
    """{company_name: 잡코리아 회원번호} (#313). 매핑된 종목은 회원번호 직접수집 → 노이즈 0.

    source_specs와 동일하게 driver 무관·루프 진입 전 1회만 조회. 실패/빈 결과 시 빈 dict
    반환 → 전 종목 키워드 폴백(graceful, 회귀 없음).
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url, echo=False, future=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text("""
                SELECT s.name, pci.company_id
                FROM hiring_portal_company_ids pci
                JOIN stocks s ON s.id = pci.stock_id
                WHERE pci.portal = 'JOBKOREA' AND s.is_target = TRUE
            """)).fetchall()
    finally:
        engine.dispose()
    return {row.name: row.company_id for row in rows}


def _load_stock_aliases(db_url: str) -> dict[str, str]:
    """{별칭(소문자) → canonical 종목명(stocks.name)} (is_target).

    canonical 은 stocks.name(공식 크롤러·잡코리아 매핑 키와 동일). 별칭 = name·short_name.
    get_target_companies 가 name+short_name 을 평면으로 주므로, 영문 'NAVER' 와 한글 '네이버'
    처럼 정규화로는 묶이지 않는 별칭을 같은 종목으로 묶어 '종목당 1회' 수집을 가능케 한다.
    실패/빈 결과 시 빈 dict → 그룹핑 없이 입력 그대로(graceful, 회귀 없음).
    """
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url, echo=False, future=True)
    try:
        with engine.connect() as conn:
            rows = conn.execute(text(
                "SELECT name, short_name FROM stocks WHERE is_target = TRUE"
            )).fetchall()
    finally:
        engine.dispose()
    alias_map: dict[str, str] = {}
    for row in rows:
        for alias in (row.name, row.short_name):
            if alias:
                alias_map[alias.lower()] = row.name
    return alias_map


def _group_companies_by_stock(
    target_companies: list[str], alias_map: dict[str, str]
) -> list[tuple[str, list[str]]]:
    """평면 회사명 리스트 → [(canonical, [검색별칭...])] (첫 등장 순서 보존).

    canonical = alias_map 로 해석(없으면 입력 그대로). 같은 종목의 풀네임·약칭을 한 그룹으로
    묶어 '종목당 1회' 반복하게 한다. 별칭은 포털 검색어로 모두 쓰여 커버리지를 유지한다.
    """
    groups: list[tuple[str, list[str]]] = []
    index: dict[str, int] = {}
    for company in target_companies:
        if not company:
            continue
        canonical = alias_map.get(company.lower(), company)
        if canonical not in index:
            index[canonical] = len(groups)
            groups.append((canonical, []))
        terms = groups[index[canonical]][1]
        if company not in terms:
            terms.append(company)
    return groups


def _instantiate_crawlers(
    specs: list[CrawlerSpec], driver, registry: dict[str, type] | None = None
) -> dict[str, object]:
    """크롤러 사양 목록 → {company_name: CrawlerInstance} (DB 접근 없음; registry 주입 시 순수).

    crawler_type = 'official_api' → driver=None (requests 전용)
    그 외 모든 타입                → driver 전달 (Selenium 필요)

    registry 를 주입하면(테스트용) 그것을 사용하고, 없으면 기본 레지스트리를 로드한다.
    드라이버 로테이션 시 동일 specs 로 driver 만 바꿔 재호출한다.
    """
    if registry is None:
        registry = _load_crawler_registry()
    crawlers: dict[str, object] = {}

    for spec in specs:
        company_name = spec.company_name
        crawler_cls_name = spec.crawler_class
        use_driver = None if spec.crawler_type == "official_api" else driver

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
            from sites import SaraminCrawler, JobkoreaCrawler, JasoseolCrawler
        except ImportError:
            from app.collectors.hiring.sites import (
                SaraminCrawler, JobkoreaCrawler, JasoseolCrawler,
            )
        # 자소설닷컴은 공개 JSON API(Selenium 불필요) — driver 무관. 런당 1회 fetch 후
        # 모듈 캐시에서 기업별 필터하므로 기업마다 재생성돼도 재fetch 하지 않는다.
        return (
            SaraminCrawler(driver=self.driver),
            JobkoreaCrawler(driver=self.driver),
            JasoseolCrawler(driver=self.driver),
        )

    # ── 수집 ─────────────────────────────────────────────────────────────────
    def collect(self, target_companies: list[str]) -> list:
        """
        전 소스에서 채용 데이터 수집 후 하나의 list[dict] 로 반환.

        드라이버 로테이션:
          driver_rotation_size(기본 3) 기업마다 Chrome 을 재시작.
          official_crawlers 는 로테이션 시 driver 참조를 갱신하기 위해 루프마다 재생성.
        """
        # 차단 신호 센서(#162 트리거 계측): 런 시작 시 카운터 초기화(크로스-런 누적 방지).
        try:
            from sites.http import block_signal_snapshot, reset_block_signals
        except ImportError:
            from app.collectors.hiring.sites.http import (
                block_signal_snapshot,
                reset_block_signals,
            )
        reset_block_signals()

        all_jobs: list[dict] = []
        # hiring_sources 사양은 driver 와 무관하므로 루프 진입 전 1회만 DB 조회.
        # 로테이션 시에는 _instantiate_crawlers 로 driver 만 갈아끼운다(DB 재조회 없음).
        source_specs: list[CrawlerSpec] = []
        official_crawlers: dict[str, object] = {}
        jobkorea_company_ids: dict[str, str] = {}   # {회사명: 잡코리아 회원번호} (#313)

        # _setup_driver 부터 try 로 감싸, 차단 신호 요약(finally)이 드라이버 초기화
        # 실패 등 어떤 예외에도 반드시 실행되게 한다(센서 측정 누락 방지).
        try:
            self._setup_driver()

            if self.use_official:
                try:
                    source_specs = _load_source_specs(self.database_url)
                    official_crawlers = _instantiate_crawlers(source_specs, self.driver)
                    logger.info("✓ hiring_sources 로드: %d개 기업 공식 크롤러", len(official_crawlers))
                except Exception as exc:
                    logger.warning("⚠️  hiring_sources 로드 실패 (공식 사이트 스킵): %s", exc)

            # 잡코리아 회원번호 매핑(#313) — 매핑 종목은 회원번호 직접수집(노이즈 0).
            if self.use_portals:
                try:
                    jobkorea_company_ids = _load_jobkorea_company_ids(self.database_url)
                    if jobkorea_company_ids:
                        logger.info(
                            "✓ 잡코리아 회원번호 매핑: %d개 종목 직접수집(나머지 키워드)",
                            len(jobkorea_company_ids),
                        )
                except Exception as exc:
                    logger.warning(
                        "⚠️  잡코리아 회원번호 매핑 로드 실패(전 종목 키워드 폴백): %s", exc
                    )

            # 별칭(풀네임·약칭)을 종목별로 묶어 '종목당 1회' 반복(중복 드라이버 재시작·로그·
            # 헛조회 제거). 커버리지 유지: 포털은 그룹의 모든 별칭으로 검색. 별칭 그룹핑은
            # 포털 검색어 커버리지에만 의미가 있으므로 use_portals 일 때만 DB 조회(공식 전용
            # 모드는 동일성 그룹핑 = 기존 거동 유지). 맵 로드 실패 시 입력 그대로(회귀 없음).
            alias_map: dict[str, str] = {}
            if self.use_portals:
                try:
                    alias_map = _load_stock_aliases(self.database_url)
                except Exception as exc:
                    logger.warning("⚠️  종목 별칭 맵 로드 실패(그룹핑 없이 진행): %s", exc)
                    alias_map = {}
            company_groups = _group_companies_by_stock(target_companies, alias_map)

            for idx, (company, search_terms) in enumerate(company_groups):

                # ── 드라이버 로테이션 ────────────────────────────────────────
                if (
                    self.driver_rotation_size > 0
                    and idx > 0
                    and idx % self.driver_rotation_size == 0
                ):
                    logger.info(
                        "🔄 드라이버 로테이션 (기업 %d/%d, 배치 크기 %d) — 재시작 중...",
                        idx + 1, len(company_groups), self.driver_rotation_size,
                    )
                    self._quit_driver()
                    self._setup_driver()
                    # 드라이버 교체 후 새 driver 참조로 재인스턴스화 (DB 재조회 없이 캐시된 specs 재사용)
                    if self.use_official and source_specs:
                        try:
                            official_crawlers = _instantiate_crawlers(
                                source_specs, self.driver
                            )
                        except Exception as exc:
                            logger.warning("⚠️  크롤러 재인스턴스화 실패: %s", exc)

                company_jobs: list[dict] = []
                logger.info("─" * 60)
                logger.info("🏢 [%d/%d] %s 수집 시작 (검색어: %s)",
                            idx + 1, len(company_groups), company, ", ".join(search_terms))

                # 1) 포털 수집 (사람인 + 잡코리아 + 자소설닷컴)
                # 포털은 그룹의 모든 별칭(풀네임·약칭)으로 검색해 커버리지를 유지한다
                # (예: jasoseol 은 한글 '네이버' 로만 매칭 — 영문 'NAVER' 로는 누락).
                if self.use_portals:
                    saramin, jobkorea, jasoseol = self._get_portal_crawlers()

                    # 사람인: 별칭마다 키워드 검색
                    for term in search_terms:
                        try:
                            results = saramin.crawl(term)
                            company_jobs.extend(results)
                            logger.info("  ✓ [%s] %d건 (%s)",
                                        saramin.source_label, len(results), term)
                        except Exception as exc:
                            logger.warning("  ⚠️  [%s] 오류(%s): %s",
                                           saramin.source_label, term, exc)
                        time.sleep(self.rate_limit_sec)

                    # 잡코리아: 회원번호 매핑(canonical) 있으면 직접수집 1회(노이즈 0, #313).
                    # 미매핑이면 별칭마다 키워드 폴백. crawl_by_member_id None(DOM깨짐) 도 별칭 폴백.
                    member_id = jobkorea_company_ids.get(company)
                    try:
                        results = (
                            jobkorea.crawl_by_member_id(member_id, company)
                            if member_id else None
                        )
                        via = "회원번호"
                        if results is None:           # 미매핑 또는 DOM 깨짐 → 별칭 키워드 폴백
                            via = "키워드"
                            results = []
                            for term in search_terms:
                                results.extend(jobkorea.crawl(term))
                                time.sleep(self.rate_limit_sec)
                        company_jobs.extend(results)
                        logger.info(
                            "  ✓ [%s] %d건 (%s)", jobkorea.source_label, len(results), via
                        )
                    except Exception as exc:
                        logger.warning("  ⚠️  [%s] 오류: %s", jobkorea.source_label, exc)
                    time.sleep(self.rate_limit_sec)

                    # 자소설닷컴: 공개 JSON API(런당 1회 fetch+기업별 필터). 별칭마다 필터.
                    for term in search_terms:
                        try:
                            results = jasoseol.crawl(term)
                            company_jobs.extend(results)
                            logger.info("  ✓ [%s] %d건 (%s)",
                                        jasoseol.source_label, len(results), term)
                        except Exception as exc:
                            logger.warning("  ⚠️  [%s] 오류(%s): %s",
                                           jasoseol.source_label, term, exc)
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
            # 차단 신호 요약(정상/예외 종료 모두 — 측정 누락 방지). >0이면 WARNING 으로
            # 띄워 로그/alerting 모니터가 #162(프록시) 재검토 트리거를 자동 포착하게 한다.
            signals = block_signal_snapshot()
            if any(signals.values()):
                logger.warning(
                    "🚧 차단 신호 감지: 403=%d 429=%d — #162(프록시 로테이션) 재검토 트리거 점검",
                    signals["403"], signals["429"],
                )
            else:
                logger.info("🛡️  차단 신호 없음 (requests 403/429=0) — 단일 IP 정상")

        logger.info("=" * 60)
        logger.info("📊 전체 수집 합계: %d건 (중복 포함)", len(all_jobs))

        # 수집단계 선거부(#176): 포털 키워드 검색은 하청·대리점·계열사 공고를 대거
        # 끌어온다(미등록 → insert 단계 _resolve_stock 이 거부). 그 거부를 parse 이전으로
        # 앞당겨 다운스트림 처리량/트래픽 낭비를 줄인다. 매칭은 insert 단계와 동일한
        # _filter_registered(공유 _match_stock_row)를 써 유효 공고 회귀를 막는다.
        return self._reject_unregistered(all_jobs)

    def _reject_unregistered(self, jobs: list[dict]) -> list[dict]:
        """stocks 미등록이 확실한 레코드를 parse 이전에 드랍(수집단계 선거부, #176).

        DB 연결 실패 시 전량 통과(graceful degradation) — insert 단계 게이트가 여전히
        방어하므로 안전하다. 효율 최적화가 수집 자체를 깨뜨리지 않도록 한다.
        """
        if not jobs:
            return jobs

        # collect()의 all_jobs 는 dict(legacy) 또는 CollectorResult(new)가 섞일 수 있다
        # (base_collector.run 과 동일 처리). 회사명은 양쪽 모두 .data/dict 에서 꺼낸다.
        def _company(item):
            data = item.data if isinstance(item, CollectorResult) else item
            return data.get("company_name")

        candidates = {c for c in (_company(j) for j in jobs) if c}
        try:
            from sqlalchemy import create_engine

            engine = create_engine(self.database_url, echo=False, future=True)
            try:
                with engine.connect() as conn:
                    registered = self._filter_registered(conn, candidates)
            finally:
                engine.dispose()
        except Exception as exc:
            logger.warning("⚠️  수집단계 선거부 스킵(전량 통과): %s", exc)
            return jobs

        kept = [j for j in jobs if _company(j) in registered]
        logger.info(
            "📊 수집단계 선거부: %d건 → %d건 (미등록 %d건 조기 제거)",
            len(jobs), len(kept), len(jobs) - len(kept),
        )
        return kept

    # ── 파싱 (이미 표준 포맷 — 그대로 통과) ─────────────────────────────────
    def parse(self, raw_data) -> dict:
        """BaseSiteCrawler._make_record() 이 이미 표준 포맷을 반환하므로 누락 키만 보완.

        필수 필드(company_name·job_title·job_link)에는 관대한 기본값을 채우지 않는다.
        누락 시 None 으로 통과시켜 insert_to_db 의 검증 게이트가 거부하도록 한다
        (빈 회사명·"(제목 없음)" 플레이스홀더가 DB 로 흘러드는 것 방지).
        """
        return {
            "source_type":     raw_data.get("source_type", "MULTI_SOURCE_WEB"),
            "company_name":    raw_data.get("company_name"),
            "job_title":       raw_data.get("job_title"),
            "job_description": raw_data.get("job_description"),
            "closing_date":    raw_data.get("closing_date"),
            "source_url":      raw_data.get("source_url") or raw_data.get("job_link"),
            "job_link":        raw_data.get("job_link") or raw_data.get("source_url"),
            "unique_key":      raw_data.get("unique_key"),
            "tech_stack":      raw_data.get("tech_stack") or [],
            "story":           raw_data.get("story"),
            "signal_strength": raw_data.get("signal_strength"),
            "posting_date":    raw_data.get("posting_date") or datetime.now(_KST).isoformat(),
        }
