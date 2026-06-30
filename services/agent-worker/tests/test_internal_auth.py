from starlette.testclient import TestClient

from app.core.config import get_settings
from app.main import create_app


def teardown_function() -> None:
    get_settings.cache_clear()


def _build_client(monkeypatch, *, token: str | None) -> TestClient:
    get_settings.cache_clear()
    if token is None:
        monkeypatch.delenv("INTERNAL_API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("INTERNAL_API_TOKEN", token)
    return TestClient(create_app())


def test_internal_routes_fail_closed_when_token_is_not_configured(monkeypatch):
    client = _build_client(monkeypatch, token=None)

    response = client.get("/internal/not-registered")

    assert response.status_code == 503
    assert response.json() == {
        "detail": {
            "code": "INTERNAL_AUTH_NOT_CONFIGURED",
            "message": "internal API token is not configured",
        }
    }


def test_internal_routes_reject_missing_or_invalid_token(monkeypatch):
    client = _build_client(monkeypatch, token="secret")

    missing = client.get("/internal/not-registered")
    invalid = client.get("/internal/not-registered", headers={"X-Internal-Token": "wrong"})

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing.json() == {
        "detail": {"code": "INTERNAL_AUTH_REQUIRED", "message": "invalid internal token"}
    }


def test_internal_routes_accept_configured_token(monkeypatch):
    client = _build_client(monkeypatch, token="secret")

    response = client.get("/internal/not-registered", headers={"X-Internal-Token": "secret"})

    assert response.status_code == 404


def test_non_internal_routes_do_not_require_internal_token(monkeypatch):
    client = _build_client(monkeypatch, token=None)

    response = client.get("/not-internal")

    assert response.status_code == 404
