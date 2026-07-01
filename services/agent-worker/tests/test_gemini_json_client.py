"""GeminiJsonClient JSON tolerance — flash-lite occasionally appends extra content
after the JSON object; the client must still recover the first valid value."""

import unittest

from app.clients.gemini_client import _loads_first_json


class LoadsFirstJsonTest(unittest.TestCase):
    def test_clean_json(self):
        self.assertEqual(_loads_first_json('{"focus": "AI", "rationale": "x"}'), {"focus": "AI", "rationale": "x"})

    def test_trailing_second_object_is_ignored(self):
        # The observed real failure: valid object followed by extra data on line 2.
        self.assertEqual(_loads_first_json('{"a": 1}\n{"b": 2}'), {"a": 1})

    def test_trailing_prose_is_ignored(self):
        self.assertEqual(_loads_first_json('{"a": 1}\n주의: 참고용입니다.'), {"a": 1})

    def test_leading_and_trailing_whitespace(self):
        self.assertEqual(_loads_first_json('\n  {"a": 1}  \n'), {"a": 1})

    def test_code_fence_is_stripped(self):
        self.assertEqual(_loads_first_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_invalid_raises_value_error(self):
        with self.assertRaises(ValueError):
            _loads_first_json("not json at all")


if __name__ == "__main__":
    unittest.main()
