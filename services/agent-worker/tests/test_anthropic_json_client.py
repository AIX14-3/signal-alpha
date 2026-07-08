"""AnthropicJsonClient — JSON tolerance, refusal guard, and end-to-end parse via
an injected fake Messages client (no network, no API key)."""

import asyncio
import unittest

from app.clients.anthropic_client import (
    AnthropicError,
    AnthropicJsonClient,
    _loads_first_json,
)


class _Block:
    def __init__(self, type_: str, text: str = "") -> None:
        self.type = type_
        self.text = text


class _Response:
    def __init__(self, content, stop_reason="end_turn") -> None:
        self.content = content
        self.stop_reason = stop_reason


class _FakeMessages:
    """Stands in for `client.messages`; records kwargs and returns a canned response."""

    def __init__(self, response=None, raises=None) -> None:
        self._response = response
        self._raises = raises
        self.last_kwargs = None

    async def create(self, **kwargs):
        self.last_kwargs = kwargs
        if self._raises is not None:
            raise self._raises
        return self._response


class _FakeClient:
    def __init__(self, messages) -> None:
        self.messages = messages


def _client(messages) -> AnthropicJsonClient:
    return AnthropicJsonClient(model="claude-sonnet-5", client=_FakeClient(messages))


class LoadsFirstJsonTest(unittest.TestCase):
    def test_clean_json(self):
        self.assertEqual(_loads_first_json('{"a": 1}'), {"a": 1})

    def test_code_fence_is_stripped(self):
        self.assertEqual(_loads_first_json('```json\n{"a": 1}\n```'), {"a": 1})

    def test_trailing_data_ignored(self):
        self.assertEqual(_loads_first_json('{"a": 1}\ntrailing'), {"a": 1})

    def test_invalid_raises_value_error(self):
        with self.assertRaises(ValueError):
            _loads_first_json("not json")


class GenerateJsonTest(unittest.TestCase):
    def test_parses_structured_response(self):
        msgs = _FakeMessages(
            _Response([_Block("text", '{"selected_ids": [2], "digest_text": "우호적 흐름"}')])
        )
        result = asyncio.run(
            _client(msgs).generate_json("prompt", schema={"type": "object"})
        )
        self.assertEqual(result, {"selected_ids": [2], "digest_text": "우호적 흐름"})
        # schema -> output_config.format; thinking disabled for cost.
        self.assertIn("output_config", msgs.last_kwargs)
        self.assertEqual(msgs.last_kwargs["thinking"], {"type": "disabled"})

    def test_refusal_raises(self):
        msgs = _FakeMessages(_Response([], stop_reason="refusal"))
        with self.assertRaises(AnthropicError):
            asyncio.run(_client(msgs).generate_json("prompt"))

    def test_sdk_error_wrapped(self):
        msgs = _FakeMessages(raises=RuntimeError("boom"))
        with self.assertRaises(AnthropicError):
            asyncio.run(_client(msgs).generate_json("prompt"))

    def test_no_text_block_raises(self):
        msgs = _FakeMessages(_Response([_Block("thinking")]))
        with self.assertRaises(AnthropicError):
            asyncio.run(_client(msgs).generate_json("prompt"))

    def test_missing_key_without_injected_client_raises(self):
        import os

        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            with self.assertRaises(AnthropicError):
                AnthropicJsonClient()
        finally:
            if saved is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved


if __name__ == "__main__":
    unittest.main()
