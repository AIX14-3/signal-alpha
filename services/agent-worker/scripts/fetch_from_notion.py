#!/usr/bin/env python
"""Notion DB/페이지를 마크다운으로 끌어온다(`push_to_notion.py`의 역방향).

리포트를 Notion 에 올리는 건 `push_to_notion.py`, 반대로 Notion 에 쌓인 리포트를
repo 로 가져와 보존/리뷰하는 게 이 스크립트다. 세 가지 명령:

    # DB 안의 페이지 목록(id·날짜·제목)
    uv run python services/agent-worker/scripts/fetch_from_notion.py list [--db-id <ID>]

    # 한 페이지를 마크다운으로 (stdout 또는 --out)
    uv run python services/agent-worker/scripts/fetch_from_notion.py get <PAGE_ID> [--out x.md]

    # DB 전체를 <dir>/<slug>.md 로 일괄 export
    uv run python services/agent-worker/scripts/fetch_from_notion.py dump --out docs/... [--db-id <ID>]

필요 환경변수 (코드에 하드코딩 금지; os.environ 우선, 없으면 repo 루트 .env 를 읽음):
    NOTION_API_KEY    Notion 인티그레이션 토큰 (secret_... 또는 ntn_...)
    NOTION_PARENT_ID  기본 대상 DB ID (--db-id 로 덮어쓸 수 있음)

블록 → 마크다운: 제목·문단·인용·코드펜스·불릿/번호 리스트·구분선·**표**·인라인(bold/code/link).
`push_to_notion.py` 가 만든 블록을 무손실에 가깝게 되돌리며, 왕복 시 생기는 인용/리스트 사이
빈 줄을 정규화한다.
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

import httpx

NOTION_VERSION = "2022-06-28"
API = "https://api.notion.com/v1"
PAGE_SIZE = 100
# scripts/ -> agent-worker/ -> services/ -> repo 루트
REPO_ROOT = Path(__file__).resolve().parents[3]


# ---------------------------------------------------------------- 환경/인증
def getenv_or_dotenv(name: str) -> str | None:
    """os.environ 우선, 없으면 repo 루트 .env 에서 읽는다(의존성 없이 수동 파싱)."""
    val = os.environ.get(name)
    if val:
        return val
    env_path = REPO_ROOT / ".env"
    if env_path.is_file():
        for ln in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, v = ln.split("=", 1)
            if k.strip() == name:
                return v.strip().strip('"').strip("'")
    return None


def headers() -> dict:
    token = getenv_or_dotenv("NOTION_API_KEY")
    if not token:
        print("ERROR: NOTION_API_KEY 가 필요합니다(환경변수 또는 .env).", file=sys.stderr)
        raise SystemExit(2)
    return {
        "Authorization": f"Bearer {token}",
        "Notion-Version": NOTION_VERSION,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------- 페이지 메타
def title_of(page: dict) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(p.get("plain_text", "") for p in prop.get("title", [])) or "(untitled)"
    return "(untitled)"


def date_of(page: dict) -> str:
    for prop in page.get("properties", {}).values():
        if prop.get("type") == "date" and prop.get("date"):
            return prop["date"].get("start", "") or ""
    return ""


def query_db(client: httpx.Client, db_id: str) -> list[dict]:
    pages, cursor = [], None
    while True:
        body: dict = {"page_size": PAGE_SIZE}
        if cursor:
            body["start_cursor"] = cursor
        r = client.post(f"{API}/databases/{db_id}/query", headers=headers(), json=body)
        if r.status_code != 200:
            print(f"ERROR: DB 쿼리 실패 {r.status_code}: {r.text[:300]}", file=sys.stderr)
            raise SystemExit(1)
        j = r.json()
        pages.extend(j.get("results", []))
        if not j.get("has_more"):
            return pages
        cursor = j.get("next_cursor")


# ---------------------------------------------------------------- 블록 → 마크다운
def rt_to_md(rich: list) -> str:
    out = []
    for r in rich:
        t = r.get("plain_text", "")
        ann = r.get("annotations", {})
        if ann.get("code"):
            t = f"`{t}`"
        if ann.get("bold"):
            t = f"**{t}**"
        if r.get("href"):
            t = f"[{t}]({r['href']})"
        out.append(t)
    return "".join(out)


def fetch_children(client: httpx.Client, block_id: str) -> list[dict]:
    blocks, cursor = [], None
    while True:
        params: dict = {"page_size": PAGE_SIZE}
        if cursor:
            params["start_cursor"] = cursor
        r = client.get(f"{API}/blocks/{block_id}/children", headers=headers(), params=params)
        if r.status_code != 200:
            print(f"WARN: 블록 조회 실패 {r.status_code}: {r.text[:200]}", file=sys.stderr)
            return blocks
        j = r.json()
        blocks.extend(j.get("results", []))
        if not j.get("has_more"):
            return blocks
        cursor = j.get("next_cursor")


def block_to_md(client: httpx.Client, b: dict) -> str:
    t = b.get("type", "")
    data = b.get(t, {})
    txt = rt_to_md(data.get("rich_text", []))
    if t == "heading_1":
        return f"# {txt}\n"
    if t == "heading_2":
        return f"## {txt}\n"
    if t == "heading_3":
        return f"### {txt}\n"
    if t == "paragraph":
        return f"{txt}\n"
    if t == "quote":
        return f"> {txt}\n"
    if t == "bulleted_list_item":
        return f"- {txt}"
    if t == "numbered_list_item":
        return f"1. {txt}"
    if t == "divider":
        return "\n---\n"
    if t == "code":
        return f"```{data.get('language', '')}\n{txt}\n```\n"
    if t == "table":
        rows = fetch_children(client, b["id"])
        lines = []
        for ri, row in enumerate(rows):
            cells = row.get("table_row", {}).get("cells", [])
            lines.append("| " + " | ".join(rt_to_md(c) for c in cells) + " |")
            if ri == 0:
                lines.append("| " + " | ".join("---" for _ in cells) + " |")
        return "\n".join(lines) + "\n"
    return txt + "\n" if txt else ""


def _normalize(md: str) -> str:
    # 왕복 시 블록마다 생기는 연속 인용/리스트 사이 빈 줄 제거
    for _ in range(6):
        md = re.sub(r"(?m)^(>.*)\n\n(?=^>)", r"\1\n", md)
        md = re.sub(r"(?m)^((?:- |\d+\. ).*)\n\n(?=^(?:- |\d+\. ))", r"\1\n", md)
    return re.sub(r"\n{3,}", "\n\n", md).rstrip() + "\n"


def page_to_md(client: httpx.Client, page_id: str) -> str:
    blocks = fetch_children(client, page_id)
    return _normalize("\n".join(m for m in (block_to_md(client, b) for b in blocks) if m))


# ---------------------------------------------------------------- slug
_ILLEGAL = re.compile(r'[\\/:*?"<>|]+')


def slugify(title: str, page_id: str) -> str:
    s = _ILLEGAL.sub(" ", title)  # 파일명 불가 문자 제거
    s = re.sub(r"\s+", "-", s.strip()).strip("-")
    s = re.sub(r"-{2,}", "-", s)
    return s or page_id.replace("-", "")


# ---------------------------------------------------------------- 명령
def cmd_list(client: httpx.Client, db_id: str) -> int:
    for pg in query_db(client, db_id):
        print(f"{date_of(pg):12s} | {pg['id']} | {title_of(pg)}")
    return 0


def cmd_get(client: httpx.Client, page_id: str, out: str | None) -> int:
    md = page_to_md(client, page_id)
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(md, encoding="utf-8")
        print(f"OK: {out}")
    else:
        print(md)
    return 0


def cmd_dump(client: httpx.Client, db_id: str, out_dir: str) -> int:
    base = Path(out_dir)
    base.mkdir(parents=True, exist_ok=True)
    pages = query_db(client, db_id)
    seen: dict[str, int] = {}
    for pg in pages:
        title = title_of(pg)
        slug = slugify(title, pg["id"])
        seen[slug] = seen.get(slug, 0) + 1
        if seen[slug] > 1:  # 제목 충돌 시 접미사
            slug = f"{slug}-{seen[slug]}"
        (base / f"{slug}.md").write_text(page_to_md(client, pg["id"]), encoding="utf-8")
        print(f"  wrote {slug}.md   <- {title}")
    print(f"DONE: {len(pages)} pages -> {out_dir}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Notion DB/페이지 → 마크다운")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="DB 페이지 목록")
    p_list.add_argument("--db-id", default=None)

    p_get = sub.add_parser("get", help="한 페이지를 md 로")
    p_get.add_argument("page_id")
    p_get.add_argument("--out", default=None)

    p_dump = sub.add_parser("dump", help="DB 전체를 md 로 일괄 export")
    p_dump.add_argument("--out", required=True, help="출력 디렉터리")
    p_dump.add_argument("--db-id", default=None)

    args = ap.parse_args()

    def resolve_db() -> str:
        db_id = getattr(args, "db_id", None) or getenv_or_dotenv("NOTION_PARENT_ID")
        if not db_id:
            print("ERROR: --db-id 또는 NOTION_PARENT_ID 가 필요합니다.", file=sys.stderr)
            raise SystemExit(2)
        return db_id

    with httpx.Client(timeout=60) as client:
        if args.cmd == "list":
            return cmd_list(client, resolve_db())
        if args.cmd == "get":
            return cmd_get(client, args.page_id, args.out)
        if args.cmd == "dump":
            return cmd_dump(client, resolve_db(), args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
