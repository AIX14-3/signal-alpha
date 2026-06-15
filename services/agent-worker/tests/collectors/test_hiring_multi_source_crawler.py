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
from unittest.mock import patch

from app.collectors.hiring import multi_source_crawler
from app.collectors.hiring.multi_source_crawler import (
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


def _spec(name: str, crawler_type: str, crawler_class: str) -> dict:
    return {"company_name": name, "crawler_type": crawler_type, "crawler_class": crawler_class}


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


if __name__ == "__main__":
    unittest.main()
