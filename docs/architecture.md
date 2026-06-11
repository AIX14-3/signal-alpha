# Architecture

Signal Alpha uses a monorepo with independently deployable services.

```text
frontend web
  -> main-server
    -> agent-worker
      -> collectors
      -> analyzers
      -> orchestrator
```

The main server remains the user-facing API boundary. The agent worker handles source collection and LLM/RAG analysis.
