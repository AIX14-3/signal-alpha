"""Unit tests for the hiring MultiSourceCrawler's crawler-map construction.

핵심 검증:
- _instantiate_crawlers 가 registry 매핑으로 올바른 클래스를 인스턴스화하는지
- official_api 타입은 driver=None, 그 외 타입은 driver 를 전달받는지
- 미등록 crawler_class 는 예외 없이 스킵되는지
- collect() 가 드라이버 로테이션을 여러 번 돌아도 _load_source_specs(DB 조회)는
  1회만 호출하고, driver 교체는 _instantiate_crawlers 재호출로만 처리하는지
  (드라이버 로테이션 최적화 회귀 방지)
"""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from app.collectors.hiring import multi_source_crawler
from app.collectors.hiring.base_collector import CollectorResult
from app.collectors.hiring.multi_source_crawler import (
    CrawlerSpec,
    MultiSourceCrawler,
    _instantiate_crawlers,
)


class _StubCrawler:
    """driver 만 받는 최소 크롤러 더블 — 실제 Selenium/네트워크 없이 인스턴스화 검증."""

    def __init__(self, driver=None):
        self.driver = driver


class _AnotherStub(_StubCrawler):
    pass


_REGISTRY = {"StubCrawler": _StubCrawler, "AnotherStub": _AnotherStub}


def _spec(name: str, crawler_type: str, crawler_class: str) -> CrawlerSpec:
    return CrawlerSpec(company_name=name, crawler_type=crawler_type, crawler_class=crawler_class)


class InstantiateCrawlersTest(unittest.TestCase):
    def test_maps_company_to_registered_class(self):
        specs = [
            _spec("삼성전자", "official_api", "StubCrawler"),
            _spec("NAVER", "official_selenium", "AnotherStub"),
        ]
        crawlers = _instantiate_crawlers(specs, driver="DRIVER", registry=_REGISTRY)

        self.assertEqual(set(crawlers), {"삼성전자", "NAVER"})
        self.assertIsInstance(crawlers["삼성전자"], _StubCrawler)
        self.assertIsInstance(crawlers["NAVER"], _AnotherStub)

    def test_official_api_gets_no_driver(self):
        """official_api 는 requests 전용 → driver=None 으로 인스턴스화."""
        specs = [_spec("삼성전자", "official_api", "StubCrawler")]
        crawlers = _instantiate_crawlers(specs, driver="DRIVER", registry=_REGISTRY)
        self.assertIsNone(crawlers["삼성전자"].driver)

    def test_non_api_type_receives_driver(self):
        """official_selenium 등은 Selenium 필요 → driver 전달."""
        specs = [_spec("NAVER", "official_selenium", "StubCrawler")]
        crawlers = _instantiate_crawlers(specs, driver="DRIVER", registry=_REGISTRY)
        self.assertEqual(crawlers["NAVER"].driver, "DRIVER")

    def test_unknown_class_is_skipped(self):
        """미등록 crawler_class 는 예외 없이 스킵되고 결과에서 제외."""
        specs = [
            _spec("삼성전자", "official_api", "StubCrawler"),
            _spec("미지의기업", "official_selenium", "DoesNotExistCrawler"),
        ]
        crawlers = _instantiate_crawlers(specs, driver="DRIVER", registry=_REGISTRY)
        self.assertEqual(set(crawlers), {"삼성전자"})

    def test_empty_specs_return_empty(self):
        self.assertEqual(_instantiate_crawlers([], driver="DRIVER", registry=_REGISTRY), {})

    def test_reinstantiation_swaps_driver_without_db(self):
        """드라이버 로테이션: 동일 specs 로 driver 만 바꿔 재인스턴스화.

        _instantiate_crawlers 는 DB 에 접근하지 않는 순수 함수이므로, 로테이션마다
        DB 재조회 없이 새 driver 참조만 반영된다(최적화 회귀 방지).
        """
        specs = [_spec("NAVER", "official_selenium", "StubCrawler")]

        first = _instantiate_crawlers(specs, driver="DRIVER_1", registry=_REGISTRY)
        second = _instantiate_crawlers(specs, driver="DRIVER_2", registry=_REGISTRY)

        self.assertEqual(first["NAVER"].driver, "DRIVER_1")
        self.assertEqual(second["NAVER"].driver, "DRIVER_2")
        # 매 호출이 새 인스턴스를 만든다(이전 driver 참조가 남지 않음).
        self.assertIsNot(first["NAVER"], second["NAVER"])


class CollectRotationDbReloadTest(unittest.TestCase):
    """collect() 로테이션 루프가 DB(_load_source_specs)를 1회만 조회하는지 검증.

    이 PR의 핵심 가치(드라이버 로테이션마다 hiring_sources 재조회 제거)를 실제 회귀
    지점인 collect() 루프에서 못 박는다. 실제 Chrome/DB 없이 모듈 함수와 드라이버
    셋업/종료를 패치해 로테이션 분기만 결정적으로 실행한다.
    """

    def test_collect_loads_specs_once_across_rotations(self):
        specs = [_spec("NAVER", "official_selenium", "StubCrawler")]

        crawler = MultiSourceCrawler(
            database_url="postgresql://test/ignored",
            use_portals=False,        # 포털 크롤러(네트워크)·sleep 경로 차단
            use_official=True,
            rate_limit_sec=0.0,
            driver_rotation_size=1,   # 기업마다 로테이션 → idx=1,2 에서 2회 발생
        )

        with patch.object(
            multi_source_crawler, "_load_source_specs", return_value=specs
        ) as mock_load, patch.object(
            multi_source_crawler, "_instantiate_crawlers", return_value={}
        ) as mock_inst, patch.object(
            MultiSourceCrawler, "_setup_driver", autospec=True
        ), patch.object(
            MultiSourceCrawler, "_quit_driver", autospec=True
        ):
            crawler.collect(["삼성전자", "NAVER", "카카오"])

        # 핵심: 로테이션이 2번 일어나도 DB 조회는 단 1회.
        self.assertEqual(mock_load.call_count, 1)
        # 최초 1회 + 로테이션 2회 = driver 만 갈아끼우며 재인스턴스화.
        self.assertEqual(mock_inst.call_count, 3)


def _crawler() -> MultiSourceCrawler:
    return MultiSourceCrawler(
        database_url="postgresql://test/ignored",
        use_portals=False,
        use_official=False,
    )


class FilterRegisteredTest(unittest.TestCase):
    """수집단계 선거부(#176)의 등록판정. insert 단계와 동일한 _match_stock_row 를
    공유하는지(회귀 방지)와, 통과 시 *원본* 회사명을 돌려주는지 검증."""

    def test_returns_only_registered_original_names(self):
        crawler = _crawler()
        registered_db = {"삼성전자", "NAVER"}

        def fake_match(_db, name):
            return (1, "전기전자") if name in registered_db else None

        with patch.object(MultiSourceCrawler, "_match_stock_row", side_effect=fake_match):
            out = crawler._filter_registered(
                db=object(), company_names={"삼성전자", "NAVER", "기아화서대리점"}
            )
        self.assertEqual(out, {"삼성전자", "NAVER"})

    def test_delegates_every_candidate_to_match_helper(self):
        """매칭 의미를 재구현하지 않고 _match_stock_row 에 전적으로 위임한다."""
        crawler = _crawler()
        with patch.object(
            MultiSourceCrawler, "_match_stock_row", return_value=None
        ) as m:
            crawler._filter_registered(db=object(), company_names={"A", "B", "C"})
        self.assertEqual(m.call_count, 3)


class ResolveStockDelegationTest(unittest.TestCase):
    """_resolve_stock 이 공유 _match_stock_row 결과를 그대로 반환하고, 미등록만 경고."""

    def test_returns_match_helper_result(self):
        crawler = _crawler()
        with patch.object(
            MultiSourceCrawler, "_match_stock_row", return_value=(7, "반도체")
        ):
            self.assertEqual(crawler._resolve_stock(object(), "삼성전자"), (7, "반도체"))

    def test_none_when_unregistered(self):
        crawler = _crawler()
        with patch.object(MultiSourceCrawler, "_match_stock_row", return_value=None):
            self.assertIsNone(crawler._resolve_stock(object(), "기아화서대리점"))


class RejectUnregisteredTest(unittest.TestCase):
    """collect() 반환 직전 선거부: 미등록 드랍·등록 보존·graceful degradation."""

    def _jobs(self):
        return [
            {"company_name": "삼성전자", "job_title": "백엔드"},
            {"company_name": "기아화서대리점", "job_title": "영업"},
            {"company_name": "NAVER", "job_title": "프론트"},
        ]

    def test_drops_unregistered_keeps_registered(self):
        crawler = _crawler()
        # create_engine 은 MagicMock 으로 대체 — 실제 DB 없이 connect() 컨텍스트 통과.
        with patch("sqlalchemy.create_engine", return_value=MagicMock()), patch.object(
            MultiSourceCrawler, "_filter_registered", return_value={"삼성전자", "NAVER"}
        ):
            kept = crawler._reject_unregistered(self._jobs())
        self.assertEqual({j["company_name"] for j in kept}, {"삼성전자", "NAVER"})
        self.assertEqual(len(kept), 2)

    def test_handles_collectorresult_items(self):
        """all_jobs 는 dict(legacy)와 CollectorResult(new)가 섞일 수 있다 — 둘 다
        회사명을 꺼내 필터링해야 한다(라이브 크롤 회귀: AttributeError 방지)."""
        crawler = _crawler()
        jobs = [
            {"company_name": "삼성전자", "job_title": "백엔드"},          # legacy dict
            CollectorResult(data={"company_name": "기아화서대리점"}),      # new, 미등록
            CollectorResult(data={"company_name": "NAVER"}),             # new, 등록
        ]
        with patch("sqlalchemy.create_engine", return_value=MagicMock()), patch.object(
            MultiSourceCrawler, "_filter_registered", return_value={"삼성전자", "NAVER"}
        ):
            kept = crawler._reject_unregistered(jobs)
        names = {(j.data if isinstance(j, CollectorResult) else j)["company_name"] for j in kept}
        self.assertEqual(names, {"삼성전자", "NAVER"})

    def test_empty_jobs_short_circuit(self):
        """빈 입력은 DB 연결 없이 즉시 반환(create_engine 미호출)."""
        crawler = _crawler()
        with patch("sqlalchemy.create_engine") as mock_engine:
            self.assertEqual(crawler._reject_unregistered([]), [])
        mock_engine.assert_not_called()

    def test_db_failure_passes_all_through(self):
        """DB 연결 실패 시 전량 통과(graceful degradation) — insert 게이트가 방어."""
        crawler = _crawler()
        jobs = self._jobs()
        with patch("sqlalchemy.create_engine", side_effect=RuntimeError("no db")):
            out = crawler._reject_unregistered(jobs)
        self.assertEqual(out, jobs)


if __name__ == "__main__":
    unittest.main()
