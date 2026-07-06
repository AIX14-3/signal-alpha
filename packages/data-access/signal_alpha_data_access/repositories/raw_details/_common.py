from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

# SQLSTATE 42703 = undefined_column. The prod DB may be stale and lack the
# patent LLM columns (``llm_features`` / ``llm_status``) added in migration 019
# (now folded into 001_baseline). The three patent-LLM methods below degrade
# gracefully when those columns are absent instead of crashing the analyzer.
_UNDEFINED_COLUMN_SQLSTATE = "42703"


def _is_undefined_column(error: Exception) -> bool:
    """True when a DB error is a missing-column error (SQLSTATE 42703).

    Matches by ``sqlstate`` so it works whether the driver surfaces it as
    ``asyncpg.exceptions.UndefinedColumnError`` or any other PostgresError.
    """
    return getattr(error, "sqlstate", None) == _UNDEFINED_COLUMN_SQLSTATE


class _RawDetailBase:
    """소스별 mixin 이 공유하는 커넥션 보관 base. ``RawDetailRepository`` 가 상속한다."""

    def __init__(self, connection: Any) -> None:
        self._connection = connection


def _jsonb(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False)
