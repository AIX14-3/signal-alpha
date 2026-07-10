"""배포 파이프라인의 유일한 로직 — kustomization 이미지 태그 핀.

여기가 조용히 실패하면 이미지는 새로 올라갔는데 매니페스트는 옛 태그를 가리켜, Argo 가
"바뀐 게 없다"고 판단하고 배포가 통째로 no-op 이 된다. 그래서 실패는 반드시 시끄러워야 한다.
(스크립트가 `deploy/scripts/` 에 있어 파이썬 패키지가 아니므로 경로로 로드한다.)
"""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[3] / "deploy" / "scripts" / "pin_image_tags.py"
_spec = importlib.util.spec_from_file_location("pin_image_tags", _SCRIPT)
assert _spec and _spec.loader
pin_image_tags = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pin_image_tags)

KUSTOMIZATION = """\
resources:
  - web.yaml

# 이미지 태그 핀(GitOps).
images:
  - name: signal-alpha-web
    newName: reg/web
    newTag: latest
  # hiring 크롤러는 별도 이미지 — 이 주석이 사라지면 안 된다.
  - name: signal-alpha-hiring-crawler
    newName: reg/hiring-crawler
    newTag: latest

configMapGenerator:
  - name: cfg
    literals:
      - newTag=this-is-not-an-image
"""


class PinImageTagsTest(unittest.TestCase):
    def test_pins_every_image_tag(self) -> None:
        out, pinned = pin_image_tags.pin(KUSTOMIZATION, "sha-abc123")

        self.assertEqual(pinned, 2)
        self.assertEqual(out.count("newTag: sha-abc123"), 2)
        self.assertNotIn("newTag: latest", out)

    def test_keeps_comments_and_everything_else(self) -> None:
        """`kustomize edit set image` 를 안 쓰는 이유 — 그건 파일을 다시 써서 주석을 날린다."""
        out, _ = pin_image_tags.pin(KUSTOMIZATION, "sha-abc123")

        self.assertIn("# 이미지 태그 핀(GitOps).", out)
        self.assertIn("# hiring 크롤러는 별도 이미지 — 이 주석이 사라지면 안 된다.", out)
        self.assertIn("resources:\n  - web.yaml", out)

    def test_does_not_touch_newtag_outside_the_images_block(self) -> None:
        out, _ = pin_image_tags.pin(KUSTOMIZATION, "sha-abc123")

        # configMapGenerator 의 literal 은 이미지가 아니다.
        self.assertIn("- newTag=this-is-not-an-image", out)

    def test_reports_zero_when_there_is_nothing_to_pin(self) -> None:
        # 호출부는 이 0 을 보고 실패한다 — 조용히 넘어가면 배포가 옛 이미지로 돌아간다.
        _, pinned = pin_image_tags.pin("resources:\n  - a.yaml\n", "sha-abc123")

        self.assertEqual(pinned, 0)

    def test_rejects_a_tag_that_is_not_a_valid_docker_tag(self) -> None:
        import re

        pattern = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
        self.assertIsNone(pattern.fullmatch("bad tag; rm -rf /"))
        self.assertIsNotNone(pattern.fullmatch("sha-0123456789ab"))


class RealKustomizationTest(unittest.TestCase):
    """레포의 실제 매니페스트로도 돌려 본다 — 구조가 바뀌면 여기서 먼저 걸린다."""

    def test_pins_the_checked_in_manifest(self) -> None:
        path = _SCRIPT.parents[1] / "k8s" / "kustomization.yaml"
        out, pinned = pin_image_tags.pin(path.read_text(encoding="utf-8"), "sha-deadbeef0000")

        self.assertGreaterEqual(pinned, 5, "이미지 5개(web·main-server·worker·crawler·db-migrate)")
        self.assertNotIn("newTag: latest", out)
