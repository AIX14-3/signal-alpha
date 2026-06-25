# Signal α — 시스템 아키텍처

> 동작의 기준은 항상 현재 코드와 테스트입니다. 이 문서는 큰 그림을 빠르게 잡기 위한 지도입니다.
> DB 스키마 기준은 `database/migrations/`, 서비스 규칙은 루트 `AGENTS.md`입니다.

## 모노레포 레이아웃

```text
signal-alpha/
  web/                    # Next.js 한국어 대시보드 (사용자 UI)
  services/
    main-server/          # FastAPI 사용자-facing API
    agent-worker/         # FastAPI 내부 수집/정규화/분석 워커 + 가격 수집 데몬
  packages/
    signal-core/          # 공통 스키마·enum·도메인 타입·안전 규칙
    data-access/          # DB repository 계층 (SQL 캡슐화)
    market-data/          # 시장 데이터 유틸
    vol-models/           # 변동성 모델 패키지
  database/
    migrations/           # PostgreSQL 스키마 (유일한 기준)
    docs/ seeds/ tests/   # 테이블 책임 문서 / seed / DB 테스트
  docs/                   # 프로젝트 문서 (본 폴더)
```

## 서비스 경계

```text
[Next.js web] ──HTTP──▶ [main-server] ───────▶  PostgreSQL  ◀─────── [agent-worker]
  (프론트엔드)            (백엔드)        api.* 읽기 view   base DML   (워커: 수집/분석)
```

> 배포는 **db / worker / backend / frontend 4유닛**으로 분리합니다. backend와 worker는 런타임에
> 직접 호출하지 않고 DB의 `api.*` 읽기 계약으로만 연결됩니다. 토폴로지·DB 소유권 경계 상세는
> [architecture-diagram.md](./architecture-diagram.md) 참고.

- **`web`** 은 `main-server` 만 호출합니다.
- **`main-server`** 는 사용자-facing API 경계: 헬스체크, 시그널 조회, 관심종목, 저널, 대시보드,
  인증, 결제/구독, 리포트, 관리자/분석 등. 현재 라우트 그룹:
  `health, signals, watchlists, journals, dashboard, reports, auth, admin, admin_auth, analytics, payments, subscriptions`.
  수집/분석 로직은 직접 들고 있지 않으며, **`agent-worker`를 직접 호출하지 않고** worker 산출물을
  DB의 `api.*` 읽기전용 view(읽기 계약)로만 읽습니다(backend/worker 런타임 분리 → 독립 배포).
- **`agent-worker`** 는 수집·정규화·분석·큐·선택적 LLM·키움 가격 수집 데몬을 담당합니다. 현재 라우트 그룹:
  `health, tasks, queue, dart, price, schedules, dead_letter, observability`.
- **`packages/data-access`** 는 재사용 가능한 repository 계층입니다. SQL을 여러 서비스에 흩뿌리지 말고
  repository를 우선 사용합니다.
- **`packages/signal-core`** 는 서비스 간 공통 데이터 계약과 안전 규칙(금지 표현 등)을 일관되게 유지합니다.

> 수집/분석 로직을 `main-server`로 옮기거나, `web`이 `agent-worker`를 직접 호출하게 만들지 않습니다
> (로컬 개발 도구가 명시적으로 요구하는 경우 제외).

## 멀티에이전트 흐름 (fan-out → aggregation)

```text
사용자 입력: stock_name / stock_code
        ↓ 표준화 (종목명 → 종목코드 / DART corp_code)
  ┌──────────────── Fan-out 병렬 분석 ────────────────┐
  │  DART Watcher   │  Report (밸류에이션)  │  Alternative Signal │
  └──────────────────────────────────────────────────┘
        ↓
  Debate Aggregation  (긍정 근거 / 주의 근거 분리 → 소스 방향성 일치도)
        ↓
  최종: source alignment + evidence + needs_review  →  final_signals  →  web 대시보드
```

소스별 수집기/분석기는 `agent-worker/app/collectors/*` 와 `analyzers/*` 아래에 소스 단위로 구성됩니다
(`dart, report, price, datalab, hiring, patent, sec`). 작업 단계별 큐 모델과 테이블 흐름은
[data-pipeline.md](./data-pipeline.md)를, 집계 계약은 `spec/final-signal-aggregator-spec.md`,
소스 에이전트 공통 계약은 `spec/source-agent-contract.md`를 참고하세요.

## 기술 스택

| 영역 | 스택 |
|---|---|
| Frontend | Next.js (Vercel 배포) |
| Main / Worker Backend | FastAPI (Python) |
| 분석 | Rule-based analyzer + 선택적 LLM 분석 (결정론 우선) |
| LLM Provider | 교체 가능한 Provider 추상화 (Gemini/OpenAI 등) |
| DB 접근 | `packages/data-access` repository |
| 의존성 관리 | **uv 워크스페이스** (`pyproject.toml` + `uv.lock` 단일 출처) |
| 로컬 인프라 | Docker Compose (postgres, 서비스들) |
| CI/CD | GitHub Actions |

설치·실행·테스트 명령은 [development.md](./development.md)를 참고하세요.

## 설계 원칙(요약)

1. mock/fixture는 테스트와 실패 fallback에만 사용하고, 기능 개발은 실제 수집 데이터 흐름을 우선합니다.
2. 수집기와 분석기는 큐 작업 타입 기준으로 분리합니다(예: DART의 `collect_dart`/`normalize_dart`/`analyze_dart`).
3. Collector는 LLM을 호출하지 않습니다. Analyzer는 정규화/승인된 **DB 데이터**를 읽고 외부 수집 API를 직접 호출하지 않습니다.
4. LLM은 고임팩트 공시 분석·리포트 텍스트 보강처럼 필요한 지점에만 선택적으로 사용하고(수치 값은 LLM이 생성하지 않음), JSON 검증 실패/타임아웃/금지 표현 감지 시 결정적 fallback을 씁니다.
5. 사용자-facing 문구는 "추천"이 아니라 "근거 확인·방향성·추가 확인 필요" 중심으로 작성합니다([overview.md](./overview.md) 가드레일).
