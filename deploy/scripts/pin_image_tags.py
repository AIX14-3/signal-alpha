#!/usr/bin/env python3
"""kustomization.yaml 의 이미지 태그를 한꺼번에 핀(pin)한다.

`kustomize edit set image` 를 쓰지 않는 이유: 그건 파일을 통째로 다시 써서 주석을 전부 날린다
(이 파일의 주석은 hiring-crawler 를 왜 따로 빌드하는지 같은, 사라지면 안 되는 맥락이다).
여기서는 `images:` 블록 안의 `newTag:` 줄만 제자리 치환한다.

usage: pin_image_tags.py <kustomization.yaml> <tag>
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# `images:` 블록의 시작과, 그 안의 항목/태그 줄. 다른 블록의 newTag 를 건드리지 않으려고
# 들여쓰기까지 함께 본다.
_IMAGES_BLOCK = re.compile(r"^images:\s*$")
_TOP_LEVEL_KEY = re.compile(r"^\S")
_NEW_TAG = re.compile(r"^(\s*newTag:\s*)(\S+)\s*$")


def pin(text: str, tag: str) -> tuple[str, int]:
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    inside = False
    pinned = 0

    for line in lines:
        if _IMAGES_BLOCK.match(line):
            inside = True
            out.append(line)
            continue
        # images: 블록은 다음 최상위 키에서 끝난다.
        if inside and _TOP_LEVEL_KEY.match(line) and not _IMAGES_BLOCK.match(line):
            inside = False

        match = _NEW_TAG.match(line) if inside else None
        if match:
            out.append(f"{match.group(1)}{tag}\n" if line.endswith("\n") else f"{match.group(1)}{tag}")
            pinned += 1
        else:
            out.append(line)

    return "".join(out), pinned


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2
    path, tag = Path(sys.argv[1]), sys.argv[2]
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", tag):
        print(f"부적절한 이미지 태그: {tag!r}", file=sys.stderr)
        return 2

    original = path.read_text(encoding="utf-8")
    updated, pinned = pin(original, tag)
    # 이미지가 하나도 안 바뀌면 배포가 조용히 옛 이미지로 돌아간다 — 실패시킨다.
    if pinned == 0:
        print(f"{path}: images: 블록에서 newTag 를 찾지 못했다", file=sys.stderr)
        return 1

    path.write_text(updated, encoding="utf-8")
    print(f"{path}: 이미지 {pinned}개를 {tag} 로 핀")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
