# Orchestrator

Coordinates a full signal analysis run.

Package boundaries:

- `queue/`: shared queue task runner, handler registry, and task type names.
- `dart/`: DART-specific task handlers, scheduling, and corp code sync orchestration.
- `persistence.py`: shared persistence helpers used by collector/analyzer flows.
- `pipeline.py`: generic collector/analyzer pipeline glue.

Expected flow:

```text
collect evidence -> analyze sources -> aggregate/debate -> persist result
```
