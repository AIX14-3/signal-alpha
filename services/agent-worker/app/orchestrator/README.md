# Orchestrator

Coordinates a full signal analysis run.

Package boundaries:

- `queue/`: shared queue task runner, handler registry, task type names, and the **queue drain
  daemon** (`drain_daemon.py`, `QUEUE_DRAIN_DAEMON_ENABLED`) that consumes `processing_queue` in
  chain order to the end (`PUBLISH_SIGNALS`) under an advisory lock (#11).
- `dart/`: DART-specific task handlers, scheduling, and corp code sync orchestration.
- `persistence.py`: shared persistence helpers used by collector/analyzer flows.
- `pipeline.py`: generic collector/analyzer pipeline glue.

Expected flow:

```text
collect evidence -> analyze sources -> aggregate -> synthesize (LLM) -> publish signals
```
