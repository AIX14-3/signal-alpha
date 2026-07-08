"""배포 드리프트 가드 — k8s CronJob/Job 이 실행하는 run_*.py 러너가 이미지에 COPY 되는지 검증.

과거 두 번(#808 community-rankings, trade-signal-overlay/trade-fills-sync) 러너를 새로 추가하면서
Dockerfile COPY 에 넣지 않아 Job 이 'python: can't open file' 로 크래시 → ArgoCD Degraded 가 났다.
이 테스트는 매니페스트가 부르는 러너 파일이 실재하면 반드시 Dockerfile 에 있어야 한다고 강제한다.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_WORKER_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _WORKER_DIR.parents[1]
_K8S_DIR = _REPO_ROOT / "deploy" / "k8s"
_DOCKERFILE = _WORKER_DIR / "Dockerfile"

_RUNNER_RE = re.compile(r"run_[a-z0-9_]+\.py")


def _manifest_runners() -> dict[str, list[str]]:
    """{러너 파일명: [해당 매니페스트 basename...]} — agent-worker 이미지에서 python run_*.py 를 부르는 것만.

    다른 이미지(예: signal-alpha-hiring-crawler)는 자체 Dockerfile 을 쓰므로 제외한다.
    command 배열 안의 run_*.py 만 본다(주석의 파일명은 무시)."""
    runners: dict[str, list[str]] = {}
    for manifest in sorted(_K8S_DIR.glob("*.yaml")):
        text = manifest.read_text(encoding="utf-8")
        if "signal-alpha-agent-worker" not in text:
            continue
        for cmd_line in re.findall(r"command:\s*\[[^\]]*\]", text):
            for runner in _RUNNER_RE.findall(cmd_line):
                runners.setdefault(runner, []).append(manifest.name)
    return runners


class DockerfileCronjobRunnersTest(unittest.TestCase):
    def test_existing_manifest_runners_are_copied_into_image(self):
        dockerfile = _DOCKERFILE.read_text(encoding="utf-8")
        missing: list[str] = []
        for runner, manifests in _manifest_runners().items():
            if not (_WORKER_DIR / runner).exists():
                # 파일이 아예 없으면 별개 문제(매니페스트가 유령 러너 참조) — 아래 테스트가 잡는다.
                continue
            if runner not in dockerfile:
                missing.append(f"{runner} (used by {', '.join(manifests)})")
        self.assertEqual(
            missing,
            [],
            f"k8s 매니페스트가 실행하는 러너가 Dockerfile COPY 에 없습니다 → Job 크래시: {missing}",
        )

    def test_manifest_runners_exist_as_files(self):
        ghosts: list[str] = []
        for runner, manifests in _manifest_runners().items():
            if not (_WORKER_DIR / runner).exists():
                ghosts.append(f"{runner} (referenced by {', '.join(manifests)})")
        self.assertEqual(
            ghosts,
            [],
            f"매니페스트가 존재하지 않는 러너 파일을 참조합니다: {ghosts}",
        )


if __name__ == "__main__":
    unittest.main()
