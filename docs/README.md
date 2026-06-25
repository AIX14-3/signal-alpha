# Signal α — 문서 인덱스

Signal α 프로젝트 문서의 진입점입니다. 새로 합류했다면 **개요 → 아키텍처 → 데이터 파이프라인** 순으로 읽으세요.

> **기준(Source of Truth)**
> - 동작/구현: 현재 브랜치의 **코드와 테스트**
> - DB 스키마: **`database/migrations/`** (문서가 아니라 마이그레이션이 기준)
> - 서비스 경계·작업 규칙: 루트 **`README.md`**, **`AGENTS.md`**
> - 과거 기획/리서치 사료: **`docs/archive/`** (현행 기준 아님)

## 핵심 문서

| 문서 | 내용 |
|---|---|
| [overview.md](./overview.md) | 제품 정의, 문제·타겟, 멀티에이전트 개요, **제품 문구 가드레일(추천 금지)**, BM/로드맵 |
| [architecture.md](./architecture.md) | 모노레포 레이아웃, 서비스 경계, 멀티에이전트 fan-out→aggregation, 기술 스택 |
| [data-pipeline.md](./data-pipeline.md) | 소스별 수집→정규화→분석→집계 큐 모델, 핵심 DB 테이블 흐름, 구현 상태 |
| [development.md](./development.md) | uv 워크스페이스 설치·실행·테스트·마이그레이션, 작업 완료 전 체크리스트 |
| [glossary.md](./glossary.md) | 도메인 용어·점수 개념·소스/에이전트 명칭·금지/권장 표현 |

## 도메인 상세 문서

| 문서 | 내용 |
|---|---|
| [datalab-keyword-validation.md](./datalab-keyword-validation.md) | LLM 키워드 생성 → 네이버 DataLab 검색량 검증 파이프라인 |
| [datalab-keyword-lifecycle.md](./datalab-keyword-lifecycle.md) | 활성 키워드 재검증·감쇠 분류·소프트 삭제 라이프사이클 |

## 운영·DB 현행 문서

| 문서 | 내용 |
|---|---|
| [db-migration-conventions.md](./db-migration-conventions.md) | DB 마이그레이션 작성 규칙·컨벤션 |
| [pre-deploy-staging-rehearsal-runbook.md](./pre-deploy-staging-rehearsal-runbook.md) | 배포 전 스테이징 리허설 런북 |

## 기술 스펙 — [`spec/`](./spec/)

수집기·분석기·집계기·API·스키마의 상세 계약. 깊은 구현 정보는 여기를 봅니다. 주요 항목:

- 데이터 레이어: `data-foundations-and-l1-l10-workflow.md`, `data-layers-l2-l10-spec.md`, `db-schema-spec.md`
- DART: `dart-collector-analyzer-spec.md`, `dart-l1-financials-spec.md`, `dart-l1-financials-impl-plan.md`, `analyzer-raw-access-conformance.md`
- Report(밸류에이션): `report-rag-current-state.md`, `report-gemini-pdf-parsing-dev-guide.md`, `report-valuation-reinterpretation-strategy.md`
- 가격: `kiwoom-rest-spec.md`
- Alternative(채용): `agent-worker-hiring.md`, `hiring-skill-enrichment-design.md`, `hiring-cutover-and-final-signals-naming.md`
- 집계/오케스트레이션: `final-signal-aggregator-spec.md`, `cross-layer-orchestration-and-risks.md`, `source-agent-contract.md`
- API/프론트: `main-server-api-spec.md`, `web-frontend-spec.md`, `web-frontend-design.md`
- 운영/연동: `third-party-integration-setup.md`

## 운영 런북 — [`runbooks/`](./runbooks/)

- `hiring-daily-schedule.md` — 채용 데이터 일일 수집/분석 스케줄 운영 절차

## 다이어그램 — [`superpowers/plans/`](./superpowers/plans/)

- 분석기 분해도, 키움 가격 분석기, 시그널 흐름 개요 (SVG)

## 아카이브 — [`archive/`](./archive/)

과거 기획서·초기 설계·리서치 노트 보존. 현행 기준 아님. → [archive/README.md](./archive/README.md)
