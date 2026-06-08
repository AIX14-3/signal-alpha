# Data Access

Shared database access package for Signal Alpha services.

## Scope

This package currently covers the MVP database flow:

```text
stocks
-> raw_documents
-> report_raw_details
-> report_chunks
-> source_documents
-> signal_events
-> signal_metrics
-> analysis_results
-> agent_results
-> final_signals
```

Expansion repositories are included so the service code can grow without duplicating SQL across
the main server and agent worker.

## Repositories

- `StockRepository`: stock lookup and ticker-based upsert.
- `CollectionRepository`: collector run logs, raw document upsert, report details, and report chunks.
- `RawDetailRepository`: DART, hiring, patent, and DataLab source-specific raw detail rows.
- `MarketDataRepository`: OHLCV and fundamental data upsert/read helpers.
- `NormalizationRepository`: source documents, signal events, signal metrics, and validation logs.
- `ProcessingQueueRepository`: worker queue enqueue, claim, success, failure, and skip updates.
- `ReportChunkRepository`: embedding-pending chunk lookup, embedding update, and pgvector similarity search.
- `AnalysisRepository`: analysis results, agent results, and final signal upsert.
- `SignalRepository`: current published signal lookup for API/UI reads.
- `UserSignalRepository`: watchlists, signal reads, and signal journals.
- `BacktestRepository`: backtest result creation, pending checks, and outcome updates.
- `ScoringRepository`: quant, TA, AI, ML, model version, and score history persistence.
- `UserBillingRepository`: users, plans, subscriptions, social accounts, PortOne verification, and terms.
- `AdminRepository`: admin accounts and sessions.

## Service Touchpoints

- `main-server` exposes `GET /signals/{ticker}` and reads through `SignalRepository`.
- `agent-worker` exposes `POST /internal/queue/{task_type}/claim` and claims work through
  `ProcessingQueueRepository`.

## Connection

Repository classes receive an asyncpg-compatible connection. The services should create a pool
once at startup and pass a connection into the repository inside a request or worker transaction.

```python
from signal_alpha_data_access import DatabaseSettings, create_pool
from signal_alpha_data_access.repositories import SignalRepository

pool = await create_pool(DatabaseSettings())

async with pool.acquire() as connection:
    repository = SignalRepository(connection)
    signal = await repository.get_current_by_ticker("005930")
```

`DATABASE_URL` is read from the environment when `DatabaseSettings()` is created without an
explicit URL.

## Local Test

This package's tests use Python standard-library `unittest` and fake asyncpg-style connections.

```powershell
cd packages\data-access
python -m unittest discover -s tests
```
