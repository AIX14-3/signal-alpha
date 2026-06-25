from __future__ import annotations

from dataclasses import dataclass
from os import getenv
from typing import Any


def _resolve_ssl_override() -> Any:
    """Read an explicit SSL mode for the asyncpg pool from the environment.

    Returns ``None`` when unset so the historical behaviour (asyncpg decides from
    the DSN/sslmode) is preserved. ``DB_SSL`` / ``DB_SSLMODE`` let a deployment be
    explicit, e.g. ``DB_SSLMODE=require`` for managed Postgres (Supabase/RDS/Neon)
    or ``DB_SSL=disable`` for a local Docker Postgres.
    """
    override = getenv("DB_SSL") or getenv("DB_SSLMODE")
    if not override:
        return None
    return False if override.lower() in {"disable", "off", "false", "0", "no"} else override


@dataclass(frozen=True)
class DatabaseSettings:
    database_url: str | None = None
    min_pool_size: int = 1
    max_pool_size: int = 10
    # SSL mode passed to asyncpg.create_pool. ``None`` (default) preserves prior
    # behaviour; falls back to the DB_SSL/DB_SSLMODE env override when unset.
    ssl: Any = None

    def __post_init__(self) -> None:
        if self.database_url is None:
            object.__setattr__(self, "database_url", getenv("DATABASE_URL"))
        if self.ssl is None:
            object.__setattr__(self, "ssl", _resolve_ssl_override())


async def create_pool(settings: DatabaseSettings | None = None) -> Any:
    resolved_settings = settings or DatabaseSettings()
    if not resolved_settings.database_url:
        raise ValueError("DATABASE_URL is required to create a database pool.")

    try:
        import asyncpg
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "asyncpg is required to create a database pool. "
            "Install the signal-alpha-data-access package dependencies first."
        ) from exc

    pool_kwargs: dict[str, Any] = {
        "dsn": resolved_settings.database_url,
        "min_size": resolved_settings.min_pool_size,
        "max_size": resolved_settings.max_pool_size,
    }
    # Only pass ssl when explicitly resolved, so the default path is byte-for-byte
    # the same call asyncpg received before this knob existed.
    if resolved_settings.ssl is not None:
        pool_kwargs["ssl"] = resolved_settings.ssl

    return await asyncpg.create_pool(**pool_kwargs)
