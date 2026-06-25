"""자소설닷컴(앵커리어) 공개 API 크롤러 단위 테스트.

네트워크 미접속: _fetch_all_postings 를 고정 샘플로 몽키패치해 매칭/레코드 빌드만 검증.
실 API 구조(employment_companies 응답)를 본뜬 샘플 사용.
"""

from __future__ import annotations

import unittest
from datetime import datetime

import app.collectors.hiring.sites.jasoseol as jaso
from app.collectors.hiring.sites.jasoseol import JasoseolCrawler, _image_urls, _norm

# 실 API employment_companies 응답을 본뜬 최소 샘플
SAMPLE = [
    {
        "id": 104762,
        "name": "안랩",
        "title": "2026 6월 신입 채용",
        "end_time": "2026-07-05T23:59:00.000+09:00",
        "employments": [
            {"field": "SW 개발(Linux)"},
            {"field": "보안 분석"},
            {"field": "SW 개발(Linux)"},  # 중복 → dedup 확인용
        ],
    },
    {"id": 200, "name": "LG생활건강", "title": "정보보안 담당자 채용", "end_time": None, "employments": []},
    {"id": 300, "name": "주택도시보증공사(HUG)", "title": "신입 채용", "end_time": None,
     "employments": [{"field": "경영"}]},
    {"id": 0, "name": "깨진공고", "title": "", "employments": []},  # title 없음 → 드랍
]


class JasoseolMatchTest(unittest.TestCase):
    def setUp(self):
        jaso.reset_cache()
        jaso._fetch_all_postings = lambda: list(SAMPLE)  # 네트워크 차단
        # crawl() 이 매칭 공고마다 상세 fetch → 폴백 경로(목록 posting 사용)로 고정.
        # 패치 scope 는 이 클래스 한정(setUp). 포스터 캡처 동작은 별도 클래스에서 검증.
        jaso._fetch_detail = lambda pid: None
        jaso._FETCH_PAUSE_SEC = 0.0                       # 테스트 속도(예의 대기 제거)
        self.c = JasoseolCrawler(driver=None)

    def tearDown(self):
        jaso.reset_cache()

    def test_two_char_exact_match(self):
        # 2글자 회사명(안랩)도 정확일치로 잡혀야 함(길이 가드에 막히면 안 됨)
        recs = self.c.crawl("안랩")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["job_title"], "2026 6월 신입 채용")

    def test_no_false_substring_match(self):
        # 'LG전자'(목표) 가 'LG생활건강'(후보) 에 잘못 매칭되면 안 됨
        self.assertEqual(self.c.crawl("LG전자"), [])
        # 실제 LG생활건강은 매칭
        self.assertEqual(len(self.c.crawl("LG생활건강")), 1)

    def test_paren_normalized_match(self):
        # 후보 '주택도시보증공사(HUG)' 의 괄호가 정규화되어 매칭
        self.assertEqual(len(self.c.crawl("주택도시보증공사")), 1)

    def test_unknown_company_returns_empty(self):
        self.assertEqual(self.c.crawl("없는회사XYZ"), [])

    def test_record_shape(self):
        r = self.c.crawl("안랩")[0]
        self.assertEqual(r["source_type"], "JASOSEOL")
        self.assertEqual(r["company_name"], "안랩")
        self.assertEqual(r["closing_date"], "2026-07-05T23:59:00.000+09:00")
        self.assertEqual(r["source_url"], "https://jasoseol.com/recruit/104762")
        self.assertEqual(r["unique_key"], "JASOSEOL|104762")
        # employments.field → 설명(중복 제거·순서 유지)
        self.assertEqual(r["job_description"], "SW 개발(Linux) / 보안 분석")

    def test_missing_title_dropped(self):
        # title 없는 '깨진공고' 는 어떤 매칭에도 레코드화되지 않음
        for rec_list in (self.c.crawl("깨진공고"), self.c.crawl("안랩")):
            for r in rec_list:
                self.assertTrue(r["job_title"])


class JasoseolDailyPosterTest(unittest.TestCase):
    """일일 crawl() 이 매칭 공고 상세를 fetch 해 포스터 image_urls + 실제 게시일을 캡처."""

    def setUp(self):
        jaso.reset_cache()
        jaso._fetch_all_postings = lambda: list(SAMPLE)  # 네트워크 차단(목록)
        jaso._FETCH_PAUSE_SEC = 0.0
        self.c = JasoseolCrawler(driver=None)

    def tearDown(self):
        jaso.reset_cache()

    def test_crawl_captures_poster_and_real_posting_date(self):
        # 매칭 공고 상세에 포스터 content + start_time 가 있으면 둘 다 레코드에 반영.
        def fake_detail(pid):
            assert pid == 104762  # 안랩 공고만 fetch 됨
            return {
                "id": pid, "name": "안랩", "title": "2026 6월 신입 채용",
                "start_time": "2026-06-10T09:00:00.000+09:00",
                "end_time": "2026-07-05T23:59:00.000+09:00",
                "employments": [{"field": "SW 개발(Linux)"}],
                "content": '<img src="https://cdn.jasoseol.com/content_images/poster.png">',
            }
        jaso._fetch_detail = fake_detail
        r = self.c.crawl("안랩")[0]
        self.assertEqual(r["image_urls"], ["https://cdn.jasoseol.com/content_images/poster.png"])
        self.assertEqual(r["posting_date"], "2026-06-10T09:00:00.000+09:00")

    def test_crawl_fallback_when_detail_missing(self):
        # 상세 fetch 실패(마감/오류 → None)면 목록 posting 으로 폴백:
        # 레코드는 유지(무음 회귀 방지), image_urls 빈 리스트, posting_date 는 now() 기본.
        jaso._fetch_detail = lambda pid: None
        recs = self.c.crawl("안랩")
        self.assertEqual(len(recs), 1)
        r = recs[0]
        self.assertEqual(r["image_urls"], [])
        # 목록 posting 에 start_time 없음 → _detail_date None → _to_record 가 now() 기본 유지.
        self.assertEqual(r["posting_date"][:4], str(datetime.now().year))


HISTORY = [
    {"id": 9001, "name": "안랩", "title": "2024 하반기 공채",
     "start_time": "2024-09-01T00:00:00.000+09:00", "end_time": "2024-09-20T18:00:00.000+09:00",
     "employments": [{"field": "보안 분석"}]},
    {"id": 9002, "name": "안랩", "title": "2023 인턴",
     "start_time": "2023-06-01T00:00:00.000+09:00", "end_time": "2023-06-15T18:00:00.000+09:00",
     "employments": []},
    {"id": 9003, "name": "안랩", "title": "",  # title 없음 → 드랍
     "start_time": "2025-01-01T00:00:00.000+09:00", "employments": []},
]


class JasoseolHistoryTest(unittest.TestCase):
    def setUp(self):
        jaso.reset_cache()
        jaso._fetch_directory = lambda: {"안랩": 123}        # name→cg_id
        jaso._fetch_company_history = lambda cg_id: list(HISTORY) if cg_id == 123 else []
        self.c = JasoseolCrawler(driver=None)

    def tearDown(self):
        jaso.reset_cache()

    def test_history_uses_real_posting_date(self):
        recs = self.c.crawl_history("안랩")
        self.assertEqual(len(recs), 2)  # 9003(제목없음) 드랍
        by_id = {r["unique_key"]: r for r in recs}
        r = by_id["JASOSEOL|9001"]
        # posting_date 가 now() 가 아니라 실제 start_time
        self.assertEqual(r["posting_date"], "2024-09-01T00:00:00.000+09:00")
        self.assertEqual(r["closing_date"], "2024-09-20T18:00:00.000+09:00")

    def test_since_year_filter(self):
        recs = self.c.crawl_history("안랩", since_year=2024)
        self.assertEqual(len(recs), 1)  # 2023 공고 제외
        self.assertEqual(recs[0]["unique_key"], "JASOSEOL|9001")

    def test_unknown_company_no_cg_id(self):
        self.assertEqual(self.c.crawl_history("없는회사"), [])


def _fake_detail(pid):
    """합성 id공간: 100~199, 25개마다 +1년(2018~2021), 13의 배수는 갭(None)."""
    if pid < 100 or pid > 199 or pid % 13 == 0:
        return None
    year = 2018 + (pid - 100) // 25
    return {"id": pid, "name": f"회사{pid}", "title": f"공고{pid}",
            "start_time": f"{year}-06-01T00:00:00.000+09:00", "end_time": None, "employments": []}


class BackfillEngineTest(unittest.TestCase):
    def setUp(self):
        jaso.reset_cache()
        jaso._fetch_detail = _fake_detail        # 네트워크 차단(합성 공간)
        jaso.find_max_id = lambda: 199

    def tearDown(self):
        jaso.reset_cache()

    def test_boundary_binary_search(self):
        # 2020 첫 id = 150((150-100)//25=2 → 2020). 경계는 그 부근, 날짜>=2020.
        b = jaso.find_boundary_id("2020-01-01", hi=199)
        self.assertEqual(jaso._detail_date(_fake_detail(b))[:4], "2020")
        self.assertLessEqual(b, 150)                 # 첫 2020 id 이하로 수렴
        self.assertEqual(_fake_detail(140)["start_time"][:4], "2019")  # 경계 아래는 2019

    def test_iter_filters_since_and_skips_gaps(self):
        got = list(jaso.iter_backfill_details(
            "2020-01-01", start_id=145, max_id=199, pause_sec=0.0))
        years = {jaso._detail_date(d)[:4] for _, d in got}
        self.assertEqual(years, {"2020", "2021"})    # since=2020 → 2019(145~149) 제외
        self.assertTrue(all(pid % 13 != 0 for pid, _ in got))  # 갭(13배수) 없음

    def test_iter_respects_max_requests(self):
        got = list(jaso.iter_backfill_details(
            "2018-01-01", start_id=100, max_id=199, pause_sec=0.0, max_requests=5))
        # 요청 5건 상한 → yield 는 그 이하(갭 제외하면 더 적을 수 있음)
        self.assertLessEqual(len(got), 5)


class DutyGroupTest(unittest.TestCase):
    def setUp(self):
        jaso.reset_cache()
        jaso._fetch_duty_taxonomy = lambda: {166: "네트워크·보안·운영", 176: "서버·백엔드개발"}
        self.c = JasoseolCrawler(driver=None)

    def tearDown(self):
        jaso.reset_cache()

    def test_duty_groups_attached_dedup(self):
        posting = {"id": 7, "name": "테스트사", "title": "백엔드 채용",
                   "employments": [{"field": "백엔드", "duty_group_ids": [176, 166, 176]}]}
        r = self.c._to_record("테스트사", "테스트사", posting)
        self.assertEqual(r["duty_group_ids"], [176, 166])           # 중복 제거·순서 유지
        self.assertEqual(r["duty_groups"], ["서버·백엔드개발", "네트워크·보안·운영"])

    def test_no_duty_ids(self):
        posting = {"id": 8, "name": "X", "title": "공고", "employments": [{"field": "a"}]}
        r = self.c._to_record("X", "X", posting)
        self.assertEqual(r["duty_groups"], [])
        self.assertEqual(r["duty_group_ids"], [])


class ImageUrlTest(unittest.TestCase):
    """#375 Phase 0: 상세 content(HTML)의 <img src> 보존(OCR enrichment 입력)."""

    def setUp(self):
        jaso.reset_cache()
        self.c = JasoseolCrawler(driver=None)

    def tearDown(self):
        jaso.reset_cache()

    def test_extracts_all_img_src_in_order_no_filter(self):
        # 필터·중복제거 없이 등장 순서대로 전부 보존(노이즈 필터는 샘플 확인 후 별도).
        content = (
            "<p>자격요건</p>"
            '<img src="https://cdn.jasoseol.com/a.png">'
            "<img alt='poster' src='https://cdn.jasoseol.com/b.jpg'>"
            '<img src="https://cdn.jasoseol.com/a.png">'  # 중복도 전부 보존
        )
        self.assertEqual(
            _image_urls({"content": content}),
            [
                "https://cdn.jasoseol.com/a.png",
                "https://cdn.jasoseol.com/b.jpg",
                "https://cdn.jasoseol.com/a.png",
            ],
        )

    def test_no_content_or_no_img_returns_empty(self):
        for posting in ({}, {"content": ""}, {"content": None}, {"content": "<p>텍스트만</p>"}):
            self.assertEqual(_image_urls(posting), [])

    def test_to_record_preserves_image_urls(self):
        posting = {
            "id": 555, "name": "테스트사", "title": "백엔드 채용",
            "employments": [{"field": "백엔드"}],
            "content": '<img src="https://cdn.jasoseol.com/poster.png">',
        }
        r = self.c._to_record("테스트사", "테스트사", posting)
        self.assertEqual(r["image_urls"], ["https://cdn.jasoseol.com/poster.png"])

    def test_to_record_no_content_empty_list(self):
        posting = {"id": 556, "name": "X", "title": "공고", "employments": []}
        r = self.c._to_record("X", "X", posting)
        self.assertEqual(r["image_urls"], [])


class NormalizeTest(unittest.TestCase):
    def test_strip_paren_space_suffix(self):
        self.assertEqual(_norm("주택도시보증공사(HUG)"), "주택도시보증공사")
        self.assertEqual(_norm("(주) 안랩 "), "안랩")
        self.assertEqual(_norm("LG 생활건강"), "lg생활건강")


if __name__ == "__main__":
    unittest.main()
