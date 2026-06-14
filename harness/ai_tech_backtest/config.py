"""Backtest configuration (env + constants).

Follows the getenv + lru_cache convention used across the repo
(spikes/toss-feasibility/config.py, services/.../core/config.py).
"""

from __future__ import annotations

from datetime import date
from functools import lru_cache
from os import getenv
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[1]
DATA_DIR = HERE / "data" / "ohlcv"
CHARTS_DIR = HERE / "charts"
REPORT_PATH = HERE / "REPORT.md"

# Forward-return horizons (trading days) to evaluate.
HORIZONS = {"1d": 1, "1w": 5, "1m": 20}

# Moves smaller than this (absolute fractional return) are labelled "flat" and
# dropped from directional scoring — avoids rewarding coin-flips on noise.
DEAD_ZONE = 0.003  # 0.3%

# Regime split. The AI era is anchored at the ChatGPT launch (2022-11-30),
# giving the recent ~2-3 year window the user asked to compare against.
AI_ERA_START = date(2022, 11, 30)

# Walk-forward windows (trading days).
TRAIN_WINDOW = 504   # ~2 years
TEST_WINDOW = 63     # ~3 months
STEP = 63            # advance test window each fold

# Round-trip cost assumption for the illustrative equity curve (fraction).
COST_PER_TRADE = 0.001  # 10 bps


class Settings:
    def __init__(self) -> None:
        self.toss_client_id = getenv("TOSS_CLIENT_ID", "")
        self.toss_client_secret = getenv("TOSS_CLIENT_SECRET", "")
        self.toss_api_base = getenv(
            "TOSS_API_BASE", "https://openapi.tossinvest.com"
        ).rstrip("/")
        self.toss_min_interval_sec = float(getenv("TOSS_MIN_REQUEST_INTERVAL_SEC", "0.25"))
        self.years = int(getenv("BACKTEST_YEARS", "10"))

        # LLM judge (reuses repo convention: openai | gemini). Optional.
        self.llm_provider = getenv("BACKTEST_LLM_PROVIDER", "gemini")
        self.llm_model = getenv("BACKTEST_LLM_MODEL", "gemini-2.0-flash")
        self.openai_api_key = getenv("OPENAI_API_KEY", "")
        self.openai_base_url = getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
        self.gemini_api_key = getenv("GEMINI_API_KEY", "")
        self.gemini_base_url = getenv(
            "GEMINI_BASE_URL", "https://generativelanguage.googleapis.com/v1beta"
        )
        self.llm_timeout_seconds = float(getenv("BACKTEST_LLM_TIMEOUT_SECONDS", "20"))
        # LLM judging is slow/costly; sample at most this many points per stock.
        self.llm_sample_per_stock = int(getenv("BACKTEST_LLM_SAMPLE", "40"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
