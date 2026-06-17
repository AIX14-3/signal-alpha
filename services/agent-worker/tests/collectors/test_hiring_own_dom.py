"""자체 DOM 공식 채용관 파서 단위 테스트 (#175: 현대·기아).

- 현대 React 채용관(ul.apply__list) / 기아(ul#applyList)의 data-* 속성 기반 파싱
- 일반/그룹 공고 상세 URL 분기
- CollectorResult 로 raw_payload·source_label 보존(4b reparse 호환)
- 공고 없음 시 빈 리스트 반환
"""

from __future__ import annotations

import unittest
from pathlib import Path

from bs4 import BeautifulSoup

from app.collectors.hiring.base_collector import CollectorResult
from app.collectors.hiring.sites.company.hyundai_kia import HyundaiCrawler, KiaCrawler

_FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "hiring"


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def _load_fixture(name: str = "HYUNDAI.html") -> str:
    return (_FIXTURE_DIR / name).read_text(encoding="utf-8")


class HyundaiParserTest(unittest.TestCase):
    def setUp(self):
        self.c = HyundaiCrawler(driver=None)  # 무인자(드라이버 없이) 파서 직접 호출
        raw = _load_fixture()
        self.results = self.c._parse_hyundai("현대자동차", _soup(raw), raw)

    def test_returns_collector_result_per_posting(self):
        self.assertEqual(len(self.results), 3)  # 픽스처 li 3건
        self.assertTrue(all(isinstance(r, CollectorResult) for r in self.results))

    def test_titles_match_data_attr(self):
        titles = [r.data["job_title"] for r in self.results]
        self.assertIn("[계약직] 글로벌 임원 비서", titles)
        self.assertIn("[계약직] 임상병리사(울산공장)", titles)
        # 플레이스홀더로 둔갑하지 않음
        for t in titles:
            self.assertTrue(t.strip())
            self.assertNotIn(t.casefold(), {"null", "undefined", "none"})

    def test_normal_posting_url(self):
        normal = next(r for r in self.results if r.data["job_title"].startswith("[계약직] 글로벌"))
        url = normal.data["source_url"]
        self.assertIn("/apply/applyView.hc", url)
        self.assertIn("recuYy=2026", url)
        self.assertIn("recuType=N5", url)
        self.assertIn("recuCls=130", url)

    def test_group_posting_url(self):
        group = next(r for r in self.results if "그룹공고" in r.data["job_title"])
        url = group.data["source_url"]
        self.assertIn("/apply/applyGroupView.hc", url)
        self.assertIn("ntcGroupNo=55", url)

    def test_closing_date_normalized_to_date(self):
        normal = next(r for r in self.results if r.data["job_title"].startswith("[계약직] 글로벌"))
        deadline = normal.data["closing_date"]
        self.assertEqual(deadline, "2026-06-18")  # dispdate 종료시각의 날짜만
        self.assertEqual(len(deadline), 10)

    def test_raw_payload_and_label_preserved(self):
        raw = _load_fixture()
        for r in self.results:
            self.assertEqual(r.raw_payload, raw)
            self.assertEqual(r.source_label, "HYUNDAI_CAREERS")


class HyundaiEmptyPageTest(unittest.TestCase):
    def test_board_empty_returns_empty_list(self):
        """공고 li 가 없고 board-empty 만 있으면 예외 없이 빈 리스트."""
        html = """
            <div class="apply__list_wrap"><div class="list">
              <ul class="apply__list"></ul>
              <article class="board-empty"><p>해당 조건에 대한 검색 결과가 없습니다.</p></article>
            </div></div>
        """
        c = HyundaiCrawler(driver=None)
        out = c._parse_hyundai("현대자동차", _soup(html), html)
        self.assertEqual(out, [])

    def test_no_list_at_all_returns_empty_list(self):
        c = HyundaiCrawler(driver=None)
        out = c._parse_hyundai("현대자동차", _soup("<div/>"), "<div/>")
        self.assertEqual(out, [])


class KiaParserTest(unittest.TestCase):
    def setUp(self):
        self.c = KiaCrawler(driver=None)
        raw = _load_fixture("KIA.html")
        self.results = self.c._parse_kia("기아", _soup(raw), raw)

    def test_returns_collector_result_per_posting(self):
        self.assertEqual(len(self.results), 3)  # 픽스처 li 3건
        self.assertTrue(all(isinstance(r, CollectorResult) for r in self.results))

    def test_titles_from_h3_tit(self):
        titles = [r.data["job_title"] for r in self.results]
        self.assertIn("[계약직] 인증중고차 매매 상담 운영", titles)
        self.assertIn("[계약직] 서비스센터 고객안내요원 (수원)", titles)
        for t in titles:
            self.assertTrue(t.strip())

    def test_normal_posting_url(self):
        normal = next(r for r in self.results if r.data["job_title"].startswith("[계약직] 인증중고차"))
        url = normal.data["source_url"]
        self.assertIn("/apply/applyView.kc", url)
        self.assertIn("recuYy=2026", url)
        self.assertIn("recuType=N5", url)
        self.assertIn("recuCls=55", url)
        self.assertNotIn("ntcGroupNo", url)

    def test_group_posting_url(self):
        group = next(r for r in self.results if "그룹공고" in r.data["job_title"])
        url = group.data["source_url"]
        self.assertIn("/apply/applyView.kc", url)  # 기아는 그룹도 applyView.kc
        self.assertIn("ntcGroupNo=42", url)

    def test_work_list_into_description(self):
        normal = next(r for r in self.results if r.data["job_title"].startswith("[계약직] 인증중고차"))
        desc = normal.data["job_description"]
        self.assertIn("국내사업", desc)
        self.assertIn("서울 압구정", desc)  # 양끝 공백 정리됨

    def test_raw_payload_and_label_preserved(self):
        raw = _load_fixture("KIA.html")
        for r in self.results:
            self.assertEqual(r.raw_payload, raw)
            self.assertEqual(r.source_label, "KIA_CAREERS")


class KiaEmptyPageTest(unittest.TestCase):
    def test_none_list_returns_empty_list(self):
        """#applyList 에 li 가 없고 #applyNoneList 만 있으면 예외 없이 빈 리스트."""
        html = """
            <div class="cont__wrap">
              <ul class="box__list" id="applyList"></ul>
              <div class="negative__box_wrap" id="applyNoneList">
                <p>현재 진행중인 채용 공고가 없습니다.</p>
              </div>
            </div>
        """
        c = KiaCrawler(driver=None)
        out = c._parse_kia("기아", _soup(html), html)
        self.assertEqual(out, [])

    def test_no_list_at_all_returns_empty_list(self):
        c = KiaCrawler(driver=None)
        out = c._parse_kia("기아", _soup("<div/>"), "<div/>")
        self.assertEqual(out, [])


if __name__ == "__main__":
    unittest.main()
