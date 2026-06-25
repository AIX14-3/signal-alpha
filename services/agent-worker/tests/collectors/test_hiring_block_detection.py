"""안티봇 차단 페이지 감지(is_blocked) 단위테스트.

차단을 '공고 없음'(빈 리스트)으로 무음 처리하던 회귀를 막는 순수 함수 가드.
"""
from __future__ import annotations

import unittest

from app.collectors.hiring.sites.base_site import BaseSiteCrawler

_block = BaseSiteCrawler.is_blocked

_SARAMIN_BLOCK = """
<html><body><h1>비정상적인 접근으로 차단되었습니다.</h1>
<div>차단 IP주소 : 112.221.66.173</div></body></html>
"""

_NORMAL = """
<html><body><div class="item_recruit"><a class="item_title">백엔드 개발자</a>
<span class="date">~07/16(목)</span></div></body></html>
"""


class IsBlockedTest(unittest.TestCase):
    def test_saramin_block_page_detected(self):
        self.assertTrue(_block(_SARAMIN_BLOCK))

    def test_block_ip_phrase_detected(self):
        self.assertTrue(_block("어떤 페이지... 차단 IP주소 : 1.2.3.4 ..."))

    def test_generic_access_denied(self):
        self.assertTrue(_block("<html><body>Access Denied</body></html>"))

    def test_normal_listing_not_blocked(self):
        self.assertFalse(_block(_NORMAL))

    def test_empty_or_none_not_blocked(self):
        self.assertFalse(_block(""))
        self.assertFalse(_block(None))


if __name__ == "__main__":
    unittest.main()
