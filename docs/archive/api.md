# API

Main server APIs should be user-facing.

Recommended initial endpoints:

```text
POST /api/watchlists
GET /api/watchlists
POST /api/signals/run/{stockCode}
GET /api/signals/latest
GET /api/signals/{signalId}
POST /api/journals
GET /api/journals
```

Agent worker APIs or jobs should be internal and called by the main server only.
