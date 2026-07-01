import asyncio

import httpx

from run_scheduler_instance import _fire, _overall_status


class FakeResponse:
    def __init__(self, payload, *, status_code=200, url="http://worker/test"):
        self._payload = payload
        self.status_code = status_code
        self.request = httpx.Request("POST", url)

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"{self.status_code} error",
                request=self.request,
                response=httpx.Response(self.status_code, request=self.request),
            )

    def json(self):
        return self._payload


class RecordingClient:
    def __init__(self, *, failures=None):
        self.posts = []
        self.failures = set(failures or [])

    async def post(self, url, *, json, headers, timeout):
        path = url.removeprefix("http://worker")
        self.posts.append(
            {
                "path": path,
                "json": json,
                "headers": headers,
                "timeout": timeout,
            }
        )
        if path in self.failures:
            return FakeResponse({"error": "boom"}, status_code=500, url=url)
        if path == "/internal/schedules/dart/collect":
            return FakeResponse({"scheduled_count": 2}, url=url)
        if path == "/internal/schedules/report/collect":
            return FakeResponse({"scheduled_count": 3}, url=url)
        if path == "/internal/price/collect":
            return FakeResponse({"status": "ok"}, url=url)
        raise AssertionError(f"Unexpected path: {path}")


def test_fire_calls_report_collect_with_default_batch_payload(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    client = RecordingClient()

    summary = asyncio.run(
        _fire(
            client,
            base_url="http://worker",
            schedule={"targets": ["report"]},
        )
    )

    assert summary == {"report": 3}
    assert client.posts == [
        {
            "path": "/internal/schedules/report/collect",
            "json": {
                "limit": 100,
                "days_back": 7,
                "max_pages": 20,
                "priority": "batch",
            },
            "headers": {"X-Internal-Token": "secret"},
            "timeout": 120.0,
        }
    ]


def test_fire_records_each_target_and_continues_after_report_failure(monkeypatch):
    monkeypatch.setenv("INTERNAL_API_TOKEN", "secret")
    client = RecordingClient(failures={"/internal/schedules/report/collect"})

    summary = asyncio.run(
        _fire(
            client,
            base_url="http://worker/",
            schedule={
                "targets": ["dart", "report", "price"],
                "dart_limit": 5,
                "price_modes": ["snapshot"],
            },
        )
    )

    assert summary["dart"] == 2
    assert summary["report"].startswith("error: ")
    assert summary["price"] == {"snapshot": "ok"}
    assert [post["path"] for post in client.posts] == [
        "/internal/schedules/dart/collect",
        "/internal/schedules/report/collect",
        "/internal/price/collect",
    ]
    assert _overall_status(summary) == "partial"
