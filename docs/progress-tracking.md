# 진행 추적 (Kick-off & Progress)

> 멘토 피드백: "각 맡은 개발 부분에 대해 kick-off 및 진행 상태가 추적되도록."
> 핵심 원칙: **담당을 미리 고정하지 않는다. 작업을 시작한 사람이 self-assign 하고,
> 그 GitHub 계정으로 추적한다.** (개발 속도가 달라도 유연)

## 한눈에

```
Kick-off 이슈 열기  →  본인 self-assign  →  Project Status: In Progress
   (무엇/완료기준)        (= 시작한 사람)
        │
        ▼
   작업 PR 열기 (본문 "Closes #N")  →  In Review  →  머지 시 자동 Done
```

## 1. Kick-off (작업 시작)

맡은 부분을 시작할 때 **Kick-off 이슈**(`🚀 Kick-off` 템플릿)를 연다. 이 이슈가
그 작업의 추적 앵커다. 양식: 영역(Area) · 목표/범위 · 설계문서 · **완료기준** ·
의존성 · 목표완료일 · 산출물.

- 이슈를 열면 **본인을 Assignee로 지정**한다 → "작업을 시작한 사람"이 GitHub ID로 박힌다.
- 영역(`area:*`)은 *무엇*을 분류할 뿐, *누구*는 항상 assignee로 본다.

## 2. 진행 상태 (GitHub Project 보드)

상태는 GitHub Project의 **Status 필드**로 추적한다:

| Status | 언제 |
| --- | --- |
| `Todo` | 이슈는 있으나 아직 시작 전 |
| `In Progress` | self-assign 하고 작업 착수 |
| `In Review` | PR 올려 리뷰 대기 |
| `Blocked` | 의존성/외부요인으로 막힘 (사유를 코멘트) |
| `Done` | PR 머지 / 완료기준 전부 충족 |

**자동화(보드 빌트인 워크플로):**
- 새 이슈/PR → 자동으로 보드에 추가
- 연결된 PR이 열리면 → `In Progress`
- PR 머지/이슈 닫힘 → `Done`

→ PR 본문에 **`Closes #이슈번호`** 를 꼭 넣어 이슈-PR을 연결한다 (자동 상태 이동의 핵심).

## 3. 보드 보는 법

- **담당자별 그룹핑**: 지금 누가 무엇을 하는지 (각 GitHub ID 기준)
- **영역(Area)별 필터**: dart/hiring/datalab/... 파트별 진척
- **Blocked 필터**: 막힌 것만 모아 빠르게 해소

## 4. 주간 스냅샷 (멘토 보고용)

보드 접근 없이도 보이도록 [`docs/STATUS.md`](./STATUS.md)에 주 1회 스냅샷을 남긴다
(수동, 또는 후속으로 GitHub Action 자동화 가능).

## 영역(Area) 정의

코드 구조 기준 — `dart · report · hiring · datalab · patent · price(kiwoom) ·
aggregator · web · db-infra · other`. (담당자 고정 아님 — 분류용)

## 라벨

- `kickoff` / `task` — 이슈 종류 (템플릿이 자동 부착)
- `area:*` — 영역 분류
- `P0`~`P3` — 우선순위 (`docs/github-project-field-options.md` 기준)
- `blocked` — 막힌 작업 (보드 Blocked와 병행 플래그)
