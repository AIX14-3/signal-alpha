import pytest

from run_scheduler_instance import _internal_headers


def test_scheduler_requires_internal_api_token(monkeypatch):
    monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)

    with pytest.raises(RuntimeError, match="INTERNAL_API_TOKEN is required"):
        _internal_headers()


def test_scheduler_sends_internal_token_header(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")

    assert _internal_headers() == {"X-Internal-Token": "secret"}
