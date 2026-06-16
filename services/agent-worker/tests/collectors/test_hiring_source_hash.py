"""Hiring source_hash 정규화 계약 테스트.

base_collector 는 raw seed 를 직접 sha256 하지 않고 공용 make_source_hash 로
SOURCE_TYPE 네임스페이스 + 정규화(trim/lowercase)를 적용한다 (Patent/DataLab 과 동일).
공백·대소문자 변형이 같은 해시로 수렴해야 source_hash UNIQUE 중복 차단이 견고해진다.
"""

import unittest

from app.collectors.hiring.base_collector import BaseCollector
from app.utils.hash_utils import make_source_hash


class HiringSourceHashTest(unittest.TestCase):
    def test_collector_namespaces_hash_with_source_type(self):
        seed = "https://example.com/job/123"
        self.assertEqual(
            make_source_hash(BaseCollector.SOURCE_TYPE, seed),
            make_source_hash("HIRING", seed),
        )

    def test_whitespace_and_case_variants_collapse(self):
        # " AbC " 와 "abc" 는 정규화 후 같은 해시 → 느슨한 중복 적재 방어.
        self.assertEqual(
            make_source_hash("HIRING", " AbC "),
            make_source_hash("HIRING", "abc"),
        )

    def test_distinct_seeds_differ(self):
        self.assertNotEqual(
            make_source_hash("HIRING", "job-a"),
            make_source_hash("HIRING", "job-b"),
        )

    def test_hash_is_64_hex_chars(self):
        h = make_source_hash("HIRING", "job-a")
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))


if __name__ == "__main__":
    unittest.main()
