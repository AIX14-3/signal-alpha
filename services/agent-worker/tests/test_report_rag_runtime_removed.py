from pathlib import Path

from app.orchestrator.queue.handlers import build_task_handlers
from app.orchestrator.queue.tasks import DEFAULT_CYCLE_PLAN


ROOT = Path(__file__).resolve().parents[3]


def test_report_embed_task_is_not_registered_in_worker_runtime():
    handlers = build_task_handlers(connection=object())

    assert "embed_report" not in DEFAULT_CYCLE_PLAN
    assert "embed_report" not in handlers


def test_report_rag_runtime_modules_are_removed():
    removed_paths = [
        ROOT / "services" / "agent-worker" / "app" / "agents" / "report" / "agent.py",
        ROOT / "services" / "agent-worker" / "app" / "agents" / "report" / "retriever.py",
    ]

    assert [path for path in removed_paths if path.exists()] == []
