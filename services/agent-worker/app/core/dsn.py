"""Canonical DSN parsing and SSL resolution for agent-worker standalone scripts.

Historically ``parse_dsn`` / ``resolve_ssl`` were copy-pasted across run_*.py
scripts, and ``run_collectors.py`` hardcoded ``ssl="require"`` which breaks a
local (non-SSL) Postgres. This module is the single source of truth: every
script imports from here so the SSL rule is decided one way.
"""
from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import unquote

# Hosts that run a local Docker/dev Postgres which rejects SSL upgrades.
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "postgres"}

_DSN_RE = re.compile(
    r"^postgres(?:ql)?://(?P<user>[^:]+):(?P<password>.*)@"
    r"(?P<host>[^:/@]+):(?P<port>\d+)/(?P<db>[^?]+)",
)


def parse_dsn(dsn: str) -> dict[str, Any]:
    """Parse a ``DATABASE_URL`` into asyncpg connect kwargs."""
    match = _DSN_RE.match(dsn)
    if not match:
        raise ValueError("Could not parse DATABASE_URL")
    return {
        "user": unquote(match.group("user")),
        "password": unquote(match.group("password")),
        "host": match.group("host"),
        "port": int(match.group("port")),
        "database": match.group("db"),
    }


def resolve_ssl(host: str) -> Any:
    """SSL mode for asyncpg.

    Managed Postgres (Supabase/RDS/Neon) needs ``ssl="require"``; a local Docker
    Postgres rejects SSL upgrades. Default by host so local testing works without
    flags. ``DB_SSL`` (alias ``DB_SSLMODE``) overrides, e.g. ``DB_SSL=disable`` /
    ``DB_SSL=require``.
    """
    override = os.getenv("DB_SSL") or os.getenv("DB_SSLMODE")
    if override:
        return False if override.lower() in {"disable", "off", "false", "0", "no"} else override
    return False if host in _LOCAL_HOSTS else "require"
