"""배포 드리프트 가드 — hiring-crawler 전용 이미지(Dockerfile.crawler)가 참조하는 ops/*.sh 및
그 스크립트가 부르는 script/*.py 러너를 실제로 COPY 하는지, bash 전용 문법을 sh로 돌리지 않는지 검증.

라이브에서 실제로 터진 두 크래시:
  1) ops/, script/ 디렉터리가 Dockerfile.crawler 에 COPY 되지 않아
     "python: can't open file 'ops/run_hiring_daily.sh'".
  2) hiring-cronjob.yaml 이 이 스크립트를 `sh`로 실행하는데 스크립트 안에 bash 전용 문법
     (${BASH_SOURCE[0]})이 있어 "Bad substitution"으로 즉시 실패.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

_WORKER_DIR = Path(__file__).resolve().parents[1]
_REPO_ROOT = _WORKER_DIR.parents[1]
_K8S_DIR = _REPO_ROOT / "deploy" / "k8s"
_DOCKERFILE_CRAWLER = _WORKER_DIR / "Dockerfile.crawler"
_HIRING_MANIFEST = _K8S_DIR / "hiring-cronjob.yaml"

_BASH_ONLY_RE = re.compile(r"\$\{[A-Za-z_][A-Za-z0-9_]*\[|\[\[ ")


def _hiring_command() -> tuple[str, str]:
    """hiring-cronjob.yaml 의 command: [interpreter, script] 반환."""
    text = _HIRING_MANIFEST.read_text(encoding="utf-8")
    match = re.search(r'command:\s*\["([^"]+)",\s*"([^"]+)"\]', text)
    assert match, "hiring-cronjob.yaml 의 command 형식이 바뀌어 파싱 실패 — 테스트 갱신 필요"
    return match.group(1), match.group(2)


class DockerfileCrawlerOpsScriptTest(unittest.TestCase):
    def test_referenced_script_top_level_dir_is_copied(self):
        _interpreter, script_rel = _hiring_command()
        top_dir = script_rel.split("/", 1)[0]
        script_path = _WORKER_DIR / script_rel
        self.assertTrue(script_path.exists(), f"매니페스트가 존재하지 않는 스크립트를 참조: {script_rel}")

        dockerfile = _DOCKERFILE_CRAWLER.read_text(encoding="utf-8")
        self.assertIn(
            f"COPY services/agent-worker/{top_dir} ./{top_dir}",
            dockerfile,
            f"hiring-cronjob.yaml 이 실행하는 {script_rel} 의 디렉터리 '{top_dir}/' 가 "
            "Dockerfile.crawler 에 COPY 되지 않음 → Job 크래시(No such file)",
        )

    def test_script_further_references_are_copied(self):
        _interpreter, script_rel = _hiring_command()
        script_text = (_WORKER_DIR / script_rel).read_text(encoding="utf-8")
        dockerfile = _DOCKERFILE_CRAWLER.read_text(encoding="utf-8")

        referenced_dirs = set(re.findall(r"\b([a-z_]+)/[A-Za-z0-9_]+\.py\b", script_text))
        own_dir = script_rel.split("/", 1)[0]
        missing = [
            d
            for d in referenced_dirs
            if d != own_dir and f"COPY services/agent-worker/{d} ./{d}" not in dockerfile
        ]
        self.assertEqual(
            missing,
            [],
            f"{script_rel} 이 참조하는 디렉터리가 Dockerfile.crawler 에 COPY 안 됨: {missing}",
        )

    def test_bash_only_syntax_is_not_run_with_sh(self):
        interpreter, script_rel = _hiring_command()
        script_text = (_WORKER_DIR / script_rel).read_text(encoding="utf-8")
        if _BASH_ONLY_RE.search(script_text):
            self.assertEqual(
                interpreter,
                "bash",
                f"{script_rel} 은 bash 전용 문법을 쓰는데 command 인터프리터가 '{interpreter}' "
                "(sh/dash 는 지원 안 함 → 'Bad substitution')",
            )


if __name__ == "__main__":
    unittest.main()
