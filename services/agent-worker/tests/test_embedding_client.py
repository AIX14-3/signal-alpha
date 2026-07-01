"""GeminiEmbeddingClient — HTTP 계층 모킹으로 768 계약·배치·재시도·에러 검증.

gemini_client 스타일(async + urllib in thread)을 그대로 미러링한 클라이언트라,
테스트도 ``urlopen`` 을 모킹해 네트워크 없이 계약만 검증한다.
"""
from __future__ import annotations

import asyncio
import json
import unittest
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.error import HTTPError

from app.clients.embedding_client import EmbeddingError, GeminiEmbeddingClient

DIM = 768


def _run(coro):
    return asyncio.run(coro)


def _http_error(code: int) -> HTTPError:
    return HTTPError("https://generativelanguage.googleapis.com", code, "err", {}, None)  # type: ignore[arg-type]


class _FakeResp:
    """Context-manager stand-in for urlopen()'s response."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._data = json.dumps(payload).encode("utf-8")

    def __enter__(self) -> "_FakeResp":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._data


def _vec(fill: float = 0.1) -> list[float]:
    return [fill] * DIM


def _embed_body(values: list[float]) -> dict[str, Any]:
    return {"embedding": {"values": values}}


def _batch_body(vectors: list[list[float]]) -> dict[str, Any]:
    return {"embeddings": [{"values": v} for v in vectors]}


class TestEmbedSingle(unittest.TestCase):
    def setUp(self) -> None:
        self.client = GeminiEmbeddingClient(api_key="k", model="text-embedding-004")

    def test_embed_returns_768_floats(self):
        with patch("app.clients.embedding_client.urlopen", return_value=_FakeResp(_embed_body(_vec()))):
            out = _run(self.client.embed("삼성전자 목표가 상향"))
        self.assertEqual(len(out), DIM)
        self.assertTrue(all(isinstance(x, float) for x in out))

    def test_text_embedding_004_omits_output_dimensionality(self):
        captured: dict[str, Any] = {}

        def _capture(req, timeout=None):  # noqa: ARG001
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResp(_embed_body(_vec()))

        with patch("app.clients.embedding_client.urlopen", side_effect=_capture):
            _run(self.client.embed("x"))
        # text-embedding-004 is natively 768 → no truncation field.
        self.assertNotIn("outputDimensionality", captured["body"])
        self.assertEqual(captured["body"]["model"], "models/text-embedding-004")

    def test_gemini_embedding_001_sends_output_dimensionality(self):
        client = GeminiEmbeddingClient(api_key="k", model="gemini-embedding-001")
        captured: dict[str, Any] = {}

        def _capture(req, timeout=None):  # noqa: ARG001
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return _FakeResp(_embed_body(_vec()))

        with patch("app.clients.embedding_client.urlopen", side_effect=_capture):
            _run(client.embed("x"))
        self.assertEqual(captured["body"]["outputDimensionality"], DIM)

    def test_dimension_mismatch_raises(self):
        with patch("app.clients.embedding_client.urlopen", return_value=_FakeResp(_embed_body([0.1] * 512))):
            with self.assertRaises(EmbeddingError):
                _run(self.client.embed("x"))

    def test_missing_values_retries_then_raises(self):
        with patch("app.clients.embedding_client.urlopen", return_value=_FakeResp({"embedding": {}})) as m, \
                patch("app.clients.embedding_client.asyncio.sleep", new_callable=AsyncMock):
            with self.assertRaises(EmbeddingError):
                _run(self.client.embed("x"))
            # empty candidate is treated retryable → all attempts used.
            self.assertEqual(m.call_count, 3)


class TestEmbedBatch(unittest.TestCase):
    def setUp(self) -> None:
        self.client = GeminiEmbeddingClient(api_key="k", model="text-embedding-004")

    def test_empty_batch_short_circuits(self):
        with patch("app.clients.embedding_client.urlopen") as m:
            out = _run(self.client.embed_batch([]))
        self.assertEqual(out, [])
        m.assert_not_called()

    def test_batch_preserves_order_and_dim(self):
        vectors = [_vec(0.1), _vec(0.2), _vec(0.3)]
        with patch("app.clients.embedding_client.urlopen", return_value=_FakeResp(_batch_body(vectors))):
            out = _run(self.client.embed_batch(["a", "b", "c"]))
        self.assertEqual(len(out), 3)
        self.assertTrue(all(len(v) == DIM for v in out))
        self.assertEqual(out[1][0], 0.2)

    def test_batch_count_mismatch_raises(self):
        with patch("app.clients.embedding_client.urlopen", return_value=_FakeResp(_batch_body([_vec()]))):
            with self.assertRaises(EmbeddingError):
                _run(self.client.embed_batch(["a", "b"]))


class TestRetryPolicy(unittest.TestCase):
    def setUp(self) -> None:
        self.client = GeminiEmbeddingClient(api_key="k", model="text-embedding-004")

    def test_429_retries_then_raises(self):
        with patch("app.clients.embedding_client.urlopen", side_effect=_http_error(429)) as m, \
                patch("app.clients.embedding_client.asyncio.sleep", new_callable=AsyncMock) as sleep:
            with self.assertRaises(EmbeddingError):
                _run(self.client.embed("x"))
            self.assertEqual(m.call_count, 3)
            self.assertEqual(sleep.call_count, 2)

    def test_400_fails_fast(self):
        with patch("app.clients.embedding_client.urlopen", side_effect=_http_error(400)) as m, \
                patch("app.clients.embedding_client.asyncio.sleep", new_callable=AsyncMock) as sleep:
            with self.assertRaises(EmbeddingError):
                _run(self.client.embed("x"))
            self.assertEqual(m.call_count, 1)
            sleep.assert_not_called()


class TestConstruction(unittest.TestCase):
    def test_missing_api_key_raises(self):
        with patch.dict("os.environ", {"GEMINI_API_KEY": ""}, clear=False):
            with self.assertRaises(EmbeddingError):
                GeminiEmbeddingClient(api_key=None)


if __name__ == "__main__":
    unittest.main()
