"""MVP 러너 공용 부트스트랩 — 합성 시드(seed_synthetic_ohlcv)와 E2E 러너(run_pipeline)가 공유.

워크스페이스가 `uv sync` 없이도 소스에서 바로 돌도록 패키지 경로를 sys.path에 얹고(``bootstrap``),
``.env`` 를 os.environ 으로 읽어(``load_env``), asyncpg 풀을 연다(``build_pool``). DSN/SSL 로직은
``signal_alpha_data_access.create_pool`` 을 재사용한다(로컬 Docker PG는 SSL 불필요).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

# services/agent-worker (이 파일의 부모). 엔트리 스크립트가 이 경로를 먼저 sys.path에 넣어야
# 본 모듈을 import 할 수 있다. ROOT = 워크스페이스 루트(sa-wt-mvp).
AGENT_WORKER = Path(__file__).resolve().parent
ROOT = AGENT_WORKER.parents[1]

# app 모듈이 의존하는 워크스페이스 패키지 소스 디렉터리(`uv sync` 미실행 환경 대비).
_PACKAGE_PATHS = (
    AGENT_WORKER,
    ROOT / "packages" / "data-access",
    ROOT / "packages" / "vol-models",
    ROOT / "packages" / "signal-core",
    ROOT / "packages" / "market-data",
)


def bootstrap() -> None:
    """워크스페이스 패키지 소스 디렉터리를 sys.path 앞에 얹고(중복 방지) stdout 을 UTF-8 로."""
    for path in _PACKAGE_PATHS:
        text = str(path)
        if text not in sys.path:
            sys.path.insert(0, text)
    # Windows 콘솔(cp949)에서 한글/이모지 출력이 죽지 않게 — 가능하면 UTF-8 로 재구성.
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def load_env(env_path: Path | None = None) -> None:
    """``.env`` 를 os.environ 으로 로드(이미 설정된 키는 덮어쓰지 않음 — run_analyzers.py 동일 관례)."""
    path = env_path or (ROOT / ".env")
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


async def build_pool(*, max_pool_size: int = 8) -> Any:
    """DATABASE_URL(.env/os.environ) 로 asyncpg 풀 생성. 공용 create_pool 재사용."""
    from signal_alpha_data_access import DatabaseSettings, create_pool

    dsn = os.getenv("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL is required (set it in .env or the environment).")
    return await create_pool(
        DatabaseSettings(database_url=dsn, min_pool_size=1, max_pool_size=max_pool_size)
    )


async def resolve_targets(
    conn: Any,
    *,
    tickers: list[str] | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    """대상 종목 해석 — tickers 명시 시 그 순서대로, 아니면 활성 종목을 id 순으로 limit 개.

    반환: ``[{"stock_id", "ticker", "name"}, ...]``. 알 수 없는 ticker 는 조용히 제외되지 않고
    호출측이 누락을 알 수 있게 결과에서 빠진다(호출측에서 비교).
    """
    if tickers:
        rows = await conn.fetch(
            "SELECT id AS stock_id, ticker, name FROM stocks WHERE ticker = ANY($1::text[])",
            tickers,
        )
        by_ticker = {row["ticker"]: dict(row) for row in rows}
        return [by_ticker[t] for t in tickers if t in by_ticker]

    rows = await conn.fetch(
        """
        SELECT id AS stock_id, ticker, name
        FROM stocks
        WHERE is_active = TRUE
        ORDER BY id ASC
        {limit_clause}
        """.format(limit_clause="LIMIT $1" if limit else ""),
        *( [limit] if limit else [] ),
    )
    return [dict(row) for row in rows]
