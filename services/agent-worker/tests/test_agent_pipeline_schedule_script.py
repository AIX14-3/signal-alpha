import shutil
import subprocess
from pathlib import Path

import pytest


POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
SCRIPT = Path(__file__).resolve().parents[1] / "ops" / "run_agent_pipeline_schedule.ps1"


def run_schedule_script(*args: str) -> subprocess.CompletedProcess[str]:
    if POWERSHELL is None:
        pytest.skip("PowerShell is not available")
    return subprocess.run(
        [
            POWERSHELL,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(SCRIPT),
            *args,
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_dry_run_prints_schedule_and_queue_requests_without_worker() -> None:
    result = run_schedule_script(
        "-DryRun",
        "-Mode",
        "All",
        "-WorkerBaseUrl",
        "http://worker.local",
        "-DartLimit",
        "3",
        "-ReportLimit",
        "4",
        "-ReportDaysBack",
        "5",
        "-ReportMaxPages",
        "6",
        "-MaxRuns",
        "7",
    )

    assert result.returncode == 0, result.stderr
    stdout = result.stdout
    assert "DRY-RUN POST /internal/schedules/dart/collect" in stdout
    assert '"limit":3' in stdout
    assert "DRY-RUN POST /internal/schedules/report/collect" in stdout
    assert '"days_back":5' in stdout
    assert '"max_pages":6' in stdout
    # 공정 라운드로빈 drain: 고정순서 per-type run-batch 대신 단일 run-cycle 호출.
    assert "DRY-RUN POST /internal/queue/run-cycle" in stdout
    assert "/internal/queue/collect_dart/run-batch" not in stdout


def test_health_check_dry_run_and_skip_report_limit_queue_scope() -> None:
    result = run_schedule_script(
        "-DryRun",
        "-HealthCheck",
        "-Mode",
        "Drain",
        "-SkipReport",
        "-MaxRuns",
        "2",
    )

    assert result.returncode == 0, result.stderr
    stdout = result.stdout
    assert "DRY-RUN GET /health" in stdout
    # Drain 은 이제 단일 공정 run-cycle. per-type run-batch 드레인은 제거됨.
    assert "/internal/queue/run-cycle" in stdout
    assert "/internal/queue/collect_dart/run-batch" not in stdout
