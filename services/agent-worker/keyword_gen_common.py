"""Shared helpers for the DataLab keyword-generation tools (Stage 1/2).

Extracted from the retired legacy ``keyword_generator.py`` (which also created
DataLab keywords WITHOUT polarity — an unlabeled-keyword entry path that
contradicted the polarity contract). Only the generic env/DSN/stock/Gemini
helpers survive here; the polarity-aware creation path lives in
``datalab_polarity_keywords.py`` / ``datalab_polarity_refresh.py``.

Boundary unchanged: keyword generation is a pre-collection tool that may call
Gemini, writes a review artifact, and only touches the DB after explicit
``--apply``. Collectors must not import this.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from urllib.request import Request, urlopen

import asyncpg  # type: ignore[import]

ROOT = Path(__file__).resolve().parents[2]

_TRANSIENT_STATUS = {500, 502, 503, 504}


def read_url(req: Request, *, timeout: int, attempts: int = 4) -> str:
    """urlopen with retries on transient failures (5xx / connection errors).

    External APIs (Gemini, Naver) return sporadic 503s; without a retry a single
    blip aborts a whole ticker. Backoff is exponential and capped.
    """
    for i in range(attempts):
        try:
            with urlopen(req, timeout=timeout) as resp:
                return resp.read().decode("utf-8")
        except HTTPError as exc:
            if exc.code in _TRANSIENT_STATUS and i < attempts - 1:
                time.sleep(min(2 ** i, 8))
                continue
            raise
        except URLError:
            if i < attempts - 1:
                time.sleep(min(2 ** i, 8))
                continue
            raise
    raise RuntimeError("unreachable")  # pragma: no cover


def load_env() -> None:
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def parse_dsn(dsn: str) -> dict[str, Any]:
    match = re.match(
        r"^postgres(?:ql)?://(?P<user>[^:]+):(?P<password>.*)@"
        r"(?P<host>[^:/@]+):(?P<port>\d+)/(?P<db>[^?]+)",
        dsn,
    )
    if not match:
        raise ValueError("Could not parse DATABASE_URL")
    return {
        "user": unquote(match.group("user")),
        "password": unquote(match.group("password")),
        "host": match.group("host"),
        "port": int(match.group("port")),
        "database": match.group("db"),
    }


async def fetch_stock(ticker: str) -> dict[str, Any]:
    dsn = os.getenv("DATABASE_URL", "")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required.")
    conn = await asyncpg.connect(**parse_dsn(dsn))
    try:
        row = await conn.fetchrow(
            """
            SELECT
                s.id,
                s.ticker,
                s.name,
                s.market,
                s.sector,
                d.corp_name,
                d.corp_name_eng,
                d.stock_name
            FROM stocks s
            LEFT JOIN dart_corp_codes d ON d.stock_id = s.id AND d.is_active = TRUE
            WHERE s.ticker = $1
              AND s.is_active = TRUE
            """,
            ticker,
        )
    finally:
        await conn.close()
    if row is None:
        raise RuntimeError(f"Active stock not found for ticker {ticker}.")
    return dict(row)


def _call_vertex_json(prompt: str, *, timeout: float = 60) -> dict[str, Any]:
    """Vertex 경로 — AI Studio 선불 크레딧과 **별개 주머니**(GCP 결제 직결·ADC).

    AI Studio 크레딧이 마르면 429 로 키워드 생성이 통째로 멈춘다(실제 겪음).
    ``KEYWORD_LLM_PROVIDER=vertex`` 로 켠다. 토큰은 ``app.clients.vertex_client`` 의
    google-auth 경로(로컬 ADC 파일 / GKE Workload Identity)를 재사용한다.
    """
    from app.clients.vertex_client import _access_token  # 지연 import — aistudio 경로는 무의존

    project = os.getenv("GOOGLE_CLOUD_PROJECT") or os.getenv("GCP_PROJECT")
    if not project:
        raise RuntimeError("GOOGLE_CLOUD_PROJECT is required for KEYWORD_LLM_PROVIDER=vertex.")
    model = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash-lite"
    url = (
        f"https://aiplatform.googleapis.com/v1/projects/{project}/locations/global"
        f"/publishers/google/models/{model}:generateContent"
    )
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "responseMimeType": "application/json",
            # 2.5 계열 thinking 이 출력 예산을 잠식하지 않게(구조화 JSON 추출엔 불필요).
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    req = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {_access_token()}",
        },
        method="POST",
    )
    payload = json.loads(read_url(req, timeout=timeout))
    text = payload["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def call_gemini(prompt: str) -> dict[str, Any]:
    # AI Studio 크레딧 고갈(429)과 무관하게 돌릴 수 있는 GCP 결제 경로(opt-in).
    if (os.getenv("KEYWORD_LLM_PROVIDER") or "").strip().lower() == "vertex":
        return _call_vertex_json(prompt)
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for keyword generation.")
    # `or` (not getenv default): an empty env var — e.g. an unset GEMINI_MODEL secret
    # passed as "" by CI — must still fall back, or the URL has no model and 404s.
    model = os.getenv("GEMINI_MODEL") or "gemini-2.5-flash-lite"
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.4,
            "responseMimeType": "application/json",
        },
    }
    req = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    payload = json.loads(read_url(req, timeout=60))
    text = payload["candidates"][0]["content"]["parts"][0]["text"]
    return json.loads(text)


def call_gemini_grounded(prompt: str) -> str:
    """Call Gemini with the Google Search grounding tool and return plain text.

    Used to deepen news headlines with concrete facts the headline+snippet omit
    (named entities, product names, figures). Grounding (``tools``) and forced JSON
    output (``responseMimeType``) cannot be combined on this API, so this helper
    deliberately leaves the response as free text — the caller feeds the text into
    the existing JSON-producing ``call_gemini`` step as extra event context.
    """
    api_key = os.getenv("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is required for grounded keyword enrichment.")
    model = os.getenv("GEMINI_GROUNDING_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash-lite"
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{model}:generateContent?key={api_key}"
    )
    body = {
        "contents": [{"parts": [{"text": prompt}]}],
        "tools": [{"google_search": {}}],
        "generationConfig": {"temperature": 0.2},
    }
    req = Request(
        url,
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    payload = json.loads(read_url(req, timeout=90))
    parts = payload["candidates"][0]["content"].get("parts", [])
    return "".join(p.get("text", "") for p in parts).strip()
