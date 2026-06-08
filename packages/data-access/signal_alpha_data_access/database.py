from __future__ import annotations

from dataclasses import dataclass
from os import getenv
from typing import Any


@dataclass(frozen=True)
class DatabaseSettings:
    database_url: str | None = None
    min_pool_size: int = 1
    max_pool_size: int = 10

    def __post_init__(self) -> None:
        if self.database_url is None:
            object.__setattr__(self, "database_url", getenv("DATABASE_URL"))


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

    return await asyncpg.create_pool(
        dsn=resolved_settings.database_url,
        min_size=resolved_settings.min_pool_size,
        max_size=resolved_settings.max_pool_size,
    )
