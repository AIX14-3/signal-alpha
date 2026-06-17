"""make_source_hash 정규화 계약 테스트 (Source Hash Contract).

CLAUDE.md 계약:
  - SHA256, 64자 hex
  - 모든 part trim + 가능한 모든 part lower-case
  - None → "" 빈 문자열
  - "|" 로 join

공백/대소문자 변형이 동일 해시로 수렴해야 source_hash UNIQUE 중복 차단이 견고해진다.
"""

import hashlib
import unittest

from app.utils.hash_utils import make_source_hash


class MakeSourceHashContractTest(unittest.TestCase):
    def test_output_is_64_char_hex(self):
        h = make_source_hash("PATENT", "1020230001234")
        self.assertEqual(len(h), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in h))

    def test_every_part_is_trimmed_and_lowercased(self):
        # 모든 part 에 좌우 공백 + 혼합 대소문자를 넣어도 정규화된 형태와 동일해야 한다.
        messy = make_source_hash("  PATENT  ", "  AbC-123  ", "  Foo BAR  ")
        clean = make_source_hash("patent", "abc-123", "foo bar")
        self.assertEqual(messy, clean)

    def test_internal_whitespace_is_preserved(self):
        # strip 은 양끝만 제거하고 내부 공백은 보존 → 의미가 다른 값은 다른 해시.
        self.assertNotEqual(
            make_source_hash("foo bar"),
            make_source_hash("foobar"),
        )

    def test_none_becomes_empty_string(self):
        # None 과 "" 는 동일하게 빈 문자열로 정규화된다.
        self.assertEqual(
            make_source_hash("DATALAB", None, "kw"),
            make_source_hash("DATALAB", "", "kw"),
        )

    def test_none_empty_equals_explicit_join(self):
        # 계약상 None→"" 이므로 ("", "") 는 "|" join 후 "" 와 동일.
        self.assertEqual(
            make_source_hash(None, None),
            hashlib.sha256("|".join(["", ""]).encode("utf-8")).hexdigest(),
        )

    def test_non_string_parts_are_stringified(self):
        # 숫자 part 도 str 화 후 정규화 → 같은 값이면 같은 해시.
        self.assertEqual(
            make_source_hash("DATALAB", 1, "kw"),
            make_source_hash("DATALAB", "1", "kw"),
        )

    def test_pipe_join_matches_manual_hash(self):
        parts = ("patent", "abc")
        expected = hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
        self.assertEqual(make_source_hash("  PATENT ", "AbC"), expected)

    def test_distinct_inputs_differ(self):
        self.assertNotEqual(
            make_source_hash("PATENT", "1020230001234"),
            make_source_hash("PATENT", "1020230009999"),
        )


if __name__ == "__main__":
    unittest.main()
