#!/usr/bin/env python3
"""GitHub Project "No Area" 백필 — 규칙·키워드 기반 Area 분류기.

Final Project - Signal Alpha 보드(AIX14-3/projects/8)에서 Area 단일선택 필드가
비어 있는 항목을, 라벨 → 커밋 스코프 → 키워드 순의 결정적 규칙으로 분류해 채운다.
LLM을 쓰지 않으므로 추가 비용/시크릿이 없다. 모호하면 채우지 않고 "수동 검토"로 남긴다.

사용:
    python tools/backfill_area.py            # dry-run (기본): 무엇을 바꿀지 출력만
    python tools/backfill_area.py --apply    # 실제 Area 필드 반영

요구: `gh` CLI 로그인(스코프: project). 레포 체크아웃 없이도 동작한다.

규칙 테이블(아래 SCOPE_MAP / KEYWORD_RULES)이 곧 area 분류 기준 문서다.
같은 로직을 추후 CI(github-script)에서 재사용할 수 있도록 classify()를 순수 함수로 둔다.
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass

OWNER = "AIX14-3"
PROJECT_NUMBER = "8"
PROJECT_ID = "PVT_kwDOERTQkc4BZ4BQ"
AREA_FIELD_ID = "PVTSSF_lADOERTQkc4BZ4BQzhVhgHw"

# Area 단일선택 옵션명 → optionId (gh project field-list 로 확보)
AREA_OPTIONS: dict[str, str] = {
    "dart": "260110b3",
    "report": "e9d46f27",
    "hiring": "695cf86b",
    "datalab": "488f081d",
    "patent": "b916f34a",
    "price": "2eb95344",
    "aggregator": "0f8ae030",
    "web": "76bef780",
    "db-infra": "c8feaa8c",
    "main-server": "8fa45486",
    "worker": "937737e6",
    "qa": "b4573cca",
    "infra": "394d703e",
    "docs": "0bfd3bc3",
}

# 2단계: 컨벤셔널 커밋 type/scope 토큰 → Area (강신호). 단일 토큰일 때만 적용.
SCOPE_MAP: dict[str, str] = {
    "dart": "dart",
    "hiring": "hiring",
    "datalab": "datalab",
    "patent": "patent",
    "price": "price",
    "market-data": "price",
    "report": "report",
    "aggregator": "aggregator",
    "web": "web",
    "orchestrator": "worker",
    "dlq": "worker",
    "worker": "worker",
    "db": "db-infra",
    "db-infra": "db-infra",
    "infra": "infra",
    "deps": "infra",
    "ci": "infra",
    "build": "infra",
    "docs": "docs",
    "qa": "qa",
    "test": "qa",
    "main-server": "main-server",
}

# 약신호 scope: 어느 도메인인지 단정 불가 → 본문 키워드에 휩쓸리지 않게 곧장 수동으로.
WEAK_SCOPES = {"collector"}

# 3단계: 키워드 규칙 — 우선순위 순(먼저 매칭되는 것이 이김).
#   짧은 영문 토큰(ci/cd/qa/web)은 단어 경계로, 그 외/한글은 부분일치.
# (area, [정규식 패턴...]) 순서가 곧 우선순위. 도메인 신호를 deploy/kickoff(infra)보다 위에 둔다.
KEYWORD_RULES: list[tuple[str, list[str]]] = [
    ("db-infra", [r"마이그레이션", r"\bmigration\b"]),
    ("worker", [r"dedupe", r"orchestrator", r"\bdlq\b", r"dead letter",
                r"큐", r"\bqueue\b", r"normalize_", r"analyze_", r"소비자", r"핸들러"]),
    ("dart", [r"\bdart\b", r"재무", r"fnlttsinglacntall"]),
    ("patent", [r"특허", r"\bpatent\b"]),
    ("datalab", [r"datalab", r"데이터랩"]),
    ("hiring", [r"크롤러", r"채용", r"\bhiring\b"]),
    ("price", [r"시세", r"\bpsr\b", r"kiwoom", r"market[- ]?data", r"데이터소스 추상"]),
    ("report", [r"리포트", r"\breport\b"]),
    ("aggregator", [r"\baggregator\b"]),
    ("infra", [r"\bci\b", r"\bcd\b", r"배포", r"\bdeploy\b", r"github actions",
               r"\bworkflow\b", r"requirements\.txt", r"uv\.lock", r"kick[- ]?off",
               r"automation"]),
    ("web", [r"프론트", r"front[- ]?end", r"\bweb\b"]),
    ("docs", [r"문서", r"\bdocs\b", r"readme"]),
    ("qa", [r"테스트", r"\bqa\b", r"pytest"]),
]

CONV_COMMIT = re.compile(r"^(\w+)(?:\(([^)]+)\))?:", re.IGNORECASE)


@dataclass
class Decision:
    area: str | None  # 결정된 Area (없으면 수동)
    reason: str       # 근거(규칙)


def _infer(title: str, body: str) -> Decision:
    """라벨을 제외한 커밋 스코프/타입 → 키워드 추론. 모호/미매칭이면 area=None."""
    # 1) 컨벤셔널 커밋 type(scope): — 단일 토큰만 신뢰, 복수(/ ,)는 모호.
    m = CONV_COMMIT.match(title.strip())
    if m:
        ctype, scope = m.group(1).lower(), (m.group(2) or "").lower()
        if scope:
            if re.search(r"[/,]", scope):
                return Decision(None, f"복수 scope '{scope}'(모호)")
            if scope in WEAK_SCOPES:
                return Decision(None, f"약신호 scope '{scope}'")
            if scope in SCOPE_MAP:
                return Decision(SCOPE_MAP[scope], f"scope:{scope}")
        elif ctype in SCOPE_MAP:  # scope 없는 ci:/docs:/test: 등은 type으로
            return Decision(SCOPE_MAP[ctype], f"type:{ctype}")

    # 2) 키워드 — 제목을 본문보다 우선(제목이 영역을 더 정확히 가리킴).
    for where, hay in (("title", title.lower()), ("body", body.lower())):
        for area, patterns in KEYWORD_RULES:
            for pat in patterns:
                if re.search(pat, hay):
                    return Decision(area, f"kw/{where}:{pat}")

    return Decision(None, "규칙 미매칭")


def classify(title: str, body: str, labels: list[str]) -> Decision:
    """라벨 → 커밋 스코프/타입 → 키워드 순으로 Area를 결정. 모호하면 area=None."""
    title = title or ""
    body = body or ""

    label_areas = {lbl[len("area:"):] for lbl in labels if lbl.startswith("area:")}
    label_areas &= set(AREA_OPTIONS)  # 유효 옵션만

    # 단일 area 라벨 → 작성자 분류를 그대로 신뢰(최우선).
    if len(label_areas) == 1:
        return Decision(next(iter(label_areas)), "label")

    inferred = _infer(title, body)

    # 복수 area 라벨 → 단일선택이라 하나만 골라야 함. 제목/타입 추론으로 1차 영역을
    # 고르되, 그 결과가 붙은 라벨 집합 안일 때만 신뢰(밖이면 사람이 판단).
    if len(label_areas) >= 2:
        if inferred.area in label_areas:
            return Decision(inferred.area, f"multi-label·{inferred.reason}")
        return Decision(None, f"복수 area 라벨 {sorted(label_areas)} → 수동")

    # 라벨 없음 → 추론 결과 그대로.
    return inferred if inferred.area else Decision(None, f"{inferred.reason} → 수동")


def gh_json(args: list[str]) -> object:
    out = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8")
    if out.returncode != 0:
        sys.exit(f"gh {' '.join(args)} 실패:\n{out.stderr}")
    return json.loads(out.stdout)


def set_area(item_id: str, option_id: str) -> None:
    mutation = (
        "mutation($p:ID!,$i:ID!,$f:ID!,$o:String!){"
        "updateProjectV2ItemFieldValue(input:{projectId:$p,itemId:$i,fieldId:$f,"
        "value:{singleSelectOptionId:$o}}){projectV2Item{id}}}"
    )
    res = subprocess.run(
        ["gh", "api", "graphql", "-f", f"query={mutation}",
         "-f", f"p={PROJECT_ID}", "-f", f"i={item_id}",
         "-f", f"f={AREA_FIELD_ID}", "-f", f"o={option_id}"],
        capture_output=True, text=True, encoding="utf-8",
    )
    if res.returncode != 0:
        sys.exit(f"필드 설정 실패(item {item_id}):\n{res.stderr}")


def main() -> None:
    ap = argparse.ArgumentParser(description="No Area 항목 규칙 기반 백필")
    ap.add_argument("--apply", action="store_true", help="실제로 Area 필드를 설정(기본은 dry-run)")
    args = ap.parse_args()

    data = gh_json(["project", "item-list", PROJECT_NUMBER, "--owner", OWNER,
                    "--format", "json", "--limit", "500"])
    items = data["items"]
    targets = [it for it in items if not it.get("area")]

    decided: list[tuple[dict, Decision]] = []
    manual: list[tuple[dict, Decision]] = []
    for it in targets:
        c = it.get("content", {})
        d = classify(c.get("title", ""), c.get("body", ""), it.get("labels") or [])
        (decided if d.area else manual).append((it, d))

    mode = "APPLY" if args.apply else "DRY-RUN"
    print(f"[{mode}] 전체 {len(items)}건 / No Area {len(targets)}건 "
          f"→ 결정 {len(decided)} · 수동필요 {len(manual)}\n")

    print("--- 결정(채울 항목) ---")
    for it, d in sorted(decided, key=lambda x: x[0]["content"].get("number", 0)):
        c = it["content"]
        num = c.get("number", "?")
        print(f"  #{num:<4} → {d.area:<12} [{d.reason:<22}] {c.get('title','')[:54]}")
        if args.apply:
            set_area(it["id"], AREA_OPTIONS[d.area])

    print("\n--- 수동 검토 필요(건드리지 않음) ---")
    for it, d in sorted(manual, key=lambda x: x[0]["content"].get("number", 0)):
        c = it["content"]
        num = c.get("number", "?")
        print(f"  #{num:<4} ({d.reason}) {c.get('title','')[:54]}")

    if args.apply:
        print(f"\n완료: {len(decided)}건 Area 설정. 수동 {len(manual)}건 남음.")
    else:
        print(f"\n(dry-run) --apply 로 {len(decided)}건 반영. 수동 {len(manual)}건은 사람이 분류.")


if __name__ == "__main__":
    main()
