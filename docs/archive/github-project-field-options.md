# GitHub Project 필드 옵션

`docs/github-project-backlog.csv`를 GitHub Project에 등록할 때 사용할 추천 필드 값입니다.

## Priority

| 값 | 이름 | 기준 |
|---|---|---|
| P0 | 반드시 필요 | E2E 데모, 핵심 API, 핵심 파이프라인처럼 없으면 MVP가 성립하지 않는 작업 |
| P1 | 중요 | 실제 데이터 연동, 품질, 예외 처리처럼 MVP 완성도를 크게 올리는 작업 |
| P2 | 있으면 좋음 | CI/CD, 배포, 문서화처럼 발표/운영 안정성을 높이는 작업 |
| P3 | 나중에 | 백테스트, 알림, 개인화처럼 MVP 이후 확장 작업 |

## Size

| 값 | 이름 | 기준 |
|---|---|---|
| XS | 아주 작음 | 2시간 이내 |
| S | 작음 | 반나절 이내 |
| M | 보통 | 1~2일 |
| L | 큼 | 3~5일 |
| XL | 매우 큼 | 1주 이상 또는 분할 필요 |

## CSV 컬럼

| 컬럼 | 용도 |
|---|---|
| Title | GitHub Issue 또는 Project item 제목 |
| Body | 작업 설명 |
| Priority | Project의 우선순위 필드 |
| Size | Project의 작업 크기 필드 |
| Sprint | 추천 구현 구간 |
| Epic | 상위 작업 묶음 |
| Type | 작업 유형 |
| Labels | GitHub label 후보 |
| Dependencies | 먼저 끝나야 하는 작업 |
| Acceptance Criteria | 완료 기준 |

## 추천 Project 필드 설정

- `Priority`: Single select, 옵션은 `P0`, `P1`, `P2`, `P3`
- `Size`: Single select, 옵션은 `XS`, `S`, `M`, `L`, `XL`
- `Sprint`: Text 또는 Single select
- `Epic`: Text 또는 Single select
- `Type`: Single select, MVP에서는 `작업`만 사용
