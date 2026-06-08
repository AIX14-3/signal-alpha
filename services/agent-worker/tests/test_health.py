import unittest
import warnings

warnings.filterwarnings(
    "ignore",
    message="Using `httpx` with `starlette.testclient` is deprecated.*",
    category=Warning
)
from starlette.testclient import TestClient

from app.main import app


class HealthCheckTest(unittest.TestCase):
    def test_health_returns_ok_status(self) -> None:
        client = TestClient(app)

        response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ok",
                "service": "agent-worker",
                "version": "0.1.0"
            }
        )


if __name__ == "__main__":
    unittest.main()
