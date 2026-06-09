# Data Access Repositories Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a shared Python data-access package with repository functions for the Signal Alpha MVP database flow.

**Architecture:** `packages/data-access` exposes thin async repositories that use asyncpg-style `fetch`, `fetchrow`, `fetchval`, and `execute` methods. Services can inject a pool connection or transaction without each service duplicating SQL.

**Tech Stack:** Python 3.11, asyncpg-compatible connection protocol, pytest-compatible async tests.

---

### Task 1: Package Skeleton

**Files:**
- Create: `packages/data-access/pyproject.toml`
- Create: `packages/data-access/signal_alpha_data_access/__init__.py`
- Create: `packages/data-access/signal_alpha_data_access/database.py`
- Test: `packages/data-access/tests/test_database_config.py`

- [ ] **Step 1: Write the failing test**

```python
from signal_alpha_data_access.database import DatabaseSettings


def test_database_settings_reads_url_from_argument():
    settings = DatabaseSettings(database_url="postgresql://user:pass@localhost:5432/signal_alpha")

    assert settings.database_url == "postgresql://user:pass@localhost:5432/signal_alpha"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages\data-access\tests\test_database_config.py -q`

Expected: FAIL because `signal_alpha_data_access` does not exist yet.

- [ ] **Step 3: Write minimal implementation**

Create `DatabaseSettings` and lazy asyncpg pool creation in `database.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages\data-access\tests\test_database_config.py -q`

Expected: PASS.

### Task 2: Stock Repository

**Files:**
- Create: `packages/data-access/signal_alpha_data_access/repositories/stocks.py`
- Create: `packages/data-access/signal_alpha_data_access/repositories/__init__.py`
- Test: `packages/data-access/tests/test_stock_repository.py`

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from signal_alpha_data_access.repositories.stocks import StockRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 1, "ticker": "005930", "name": "삼성전자", "market": "KOSPI"}


@pytest.mark.asyncio
async def test_get_by_ticker_normalizes_ticker():
    connection = FakeConnection()
    repository = StockRepository(connection)

    row = await repository.get_by_ticker(" 005930 ")

    assert row["ticker"] == "005930"
    assert connection.calls[0][2] == ("005930",)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages\data-access\tests\test_stock_repository.py -q`

Expected: FAIL because `StockRepository` does not exist.

- [ ] **Step 3: Write minimal implementation**

Implement `get_by_ticker`, `list_active`, and `ensure_stock`.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages\data-access\tests\test_stock_repository.py -q`

Expected: PASS.

### Task 3: Collection Repository

**Files:**
- Create: `packages/data-access/signal_alpha_data_access/repositories/collection.py`
- Test: `packages/data-access/tests/test_collection_repository.py`

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from signal_alpha_data_access.repositories.collection import CollectionRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return 10


@pytest.mark.asyncio
async def test_create_collector_run_uses_running_status_defaults():
    connection = FakeConnection()
    repository = CollectionRepository(connection)

    run_id = await repository.create_collector_run("REPORT", "batch")

    assert run_id == 10
    assert connection.calls[0][2] == ("REPORT", "batch")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages\data-access\tests\test_collection_repository.py -q`

Expected: FAIL because `CollectionRepository` does not exist.

- [ ] **Step 3: Write minimal implementation**

Implement collector run creation/completion, raw document upsert, report detail upsert, and report chunk replacement.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages\data-access\tests\test_collection_repository.py -q`

Expected: PASS.

### Task 4: Analysis And Signal Repositories

**Files:**
- Create: `packages/data-access/signal_alpha_data_access/repositories/analysis.py`
- Create: `packages/data-access/signal_alpha_data_access/repositories/signals.py`
- Test: `packages/data-access/tests/test_analysis_signal_repositories.py`

- [ ] **Step 1: Write the failing tests**

```python
import pytest

from signal_alpha_data_access.repositories.signals import SignalRepository


class FakeConnection:
    def __init__(self):
        self.calls = []

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": 7, "ticker": "005930", "signal": "neutral"}


@pytest.mark.asyncio
async def test_get_current_by_ticker_filters_to_current_published_signal():
    connection = FakeConnection()
    repository = SignalRepository(connection)

    row = await repository.get_current_by_ticker("005930")

    assert row["id"] == 7
    assert "final_signals.is_current = TRUE" in connection.calls[0][1]
    assert "final_signals.is_published = TRUE" in connection.calls[0][1]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest packages\data-access\tests\test_analysis_signal_repositories.py -q`

Expected: FAIL because `SignalRepository` does not exist.

- [ ] **Step 3: Write minimal implementation**

Implement analysis result creation, agent result upsert, final signal upsert, and current signal lookup.

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest packages\data-access\tests\test_analysis_signal_repositories.py -q`

Expected: PASS.

### Task 5: Documentation And Full Verification

**Files:**
- Modify: `packages/data-access/README.md`

- [ ] **Step 1: Document repository scope**

Update the README with package responsibilities, expected DB URL env var, and examples.

- [ ] **Step 2: Run all data-access tests**

Run: `python -m pytest packages\data-access\tests -q`

Expected: all tests pass.
