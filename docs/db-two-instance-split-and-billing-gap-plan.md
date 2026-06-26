# DB 2-인스턴스 물리 분리 + 프론트엔드 갭 마이그레이션 계획

> 상태: 제안(Proposal) — 본 PR은 **계획 문서만** 포함한다. 구현은 후속 PR로 단계별 진행한다.

## 1. 배경 (왜)

현재 signal-alpha 는 **단일 PostgreSQL** 위에서 `public` 스키마에 두 종류의 테이블이 섞여 있다.

- **워커 수집/분석 산출물**: `stocks`, `signal_events`, `analysis_results`, `final_signals`, `processing_queue`, `dart_*` …
- **백엔드 서비스 테이블**: `users`, `signal_subscriptions`, `subscription_plans`, `portone_verifications`, `watchlists`, `signal_journals`, `admin_accounts`, `admin_sessions` …

분리는 현재 **논리적**일 뿐이다 — `api.*` 읽기 전용 view + 2개 롤(`signal_worker` / `signal_backend`)로
백엔드가 워커 산출물을 view 로만 읽는다(`20260625_1343_api_schema_read_contract.sql`,
`20260625_1343_db_roles_and_grants.sql`).

요구사항:

1. **물리적 2-DB 분리** — ① 워커 "수집 DB", ② 백엔드 "서비스 DB"(회원/관리자/결제 포함).
   워커가 끝낸 산출물을 백엔드 DB로 넘긴다.
2. **프론트엔드 변경이 DB에 반영 안 되는 갭 해소** — 회원 추가/수정/삭제, 결제 취소·환불 이력,
   구독 시작일/결제일을 저장할 신규 테이블·컬럼·마이그레이션 추가.

## 2. 확정된 설계 결정

- 별도 Postgres **인스턴스 2개** (단일 인스턴스 내 2 DB 아님).
- 워커 산출물은 **앱레벨 발행(publish)** 으로 백엔드 DB에 기록 — 물리 분리 시 cross-DB FK/JOIN 불가.
- **그린필드** — 기존 단일 DB 데이터 백필 없음, 신규 스키마로 부트스트랩.
- 이력은 **신규 테이블 추가** 로 모델링.

## 3. 확인된 프론트엔드 → DB 갭 (마이그레이션 근거)

| 갭 | 현상 | 원인 |
|---|---|---|
| 회원 추가 | 관리자 회원 생성 불가 | `routes/admin.py` 에 PATCH/DELETE 만 있고 POST 없음 (가입은 `auth.py` 경유만) |
| 결제/환불 이력 | 환불 시 원 결제행이 사라짐 | `record_portone_verification` 이 `ON CONFLICT(imp_uid) DO UPDATE` 로 덮어씀. `list_payment_verifications` 는 `status='paid'` 만 필터해 환불 숨김. 금액/취소시각/사유 컬럼 없음 |
| 구독 날짜 | 다음 결제일/자동갱신 저장 불가 | 관리자는 `expires_at` 만 설정 가능. `next_billing_at`/`auto_renew` 없음 |
| 감사 로그 | 누가 무엇을 바꿨는지 기록 없음 | audit trail 테이블 부재 |

## 4. 작업 그룹

### Group A — 백엔드 DB 갭 마이그레이션 + CRUD 배선 (독립적, 우선)

신규 마이그레이션 (`database/migrations/`, 타임스탬프 네이밍):

1. `payments` — 결제/환불 append-only 이력
   (`amount`, `status`, `paid_at`, `cancelled_at`, `refund_amount`, `cancel_reason`, `raw_response` …).
2. `signal_subscriptions` 컬럼 추가: `next_billing_at`, `auto_renew`. (`started_at` 은 이미 존재)
3. `users` 컬럼 추가: `status`(active/suspended/deleted). (`deleted_at` 은 이미 존재)
4. `admin_audit_log` — 관리자 변경 before/after 감사 로그.

코드:

- `services/main-server/app/api/routes/admin.py`: POST 회원 생성, soft-delete 시 `status` 동기화,
  PATCH/DELETE/구독/환불 시 `admin_audit_log` 기록.
- `packages/data-access/.../repositories/users_billing.py`: `record_payment`/`record_refund`/
  `list_payments`/`get_latest_paid_payment` 추가, `create_subscription` 에 결제일 파라미터 추가,
  `insert_member`/`set_user_status`/`update_subscription_dates` 추가.
- `packages/data-access/.../repositories/admin.py`: `record_audit_log`/`list_audit_logs`.
- `services/main-server/app/api/routes/payments.py`: 결제 성공·환불을 `payments` 이력에 기록,
  `/history` 가 환불 포함.

> 참고: Group A 는 단일 DB·2-DB 양쪽에서 동작한다(백엔드 소유 테이블이므로). 그린필드 컷오버를 기다리지 않고 먼저 반영 가능.

### Group B — 물리적 2-DB 분리 + 워커 발행

- **설정 분리**: 워커 `services/agent-worker/app/core/config.py` → 수집 DB DSN.
  메인서버 `services/main-server/app/core/config.py` → 백엔드 DB DSN.
  워커에 백엔드 DB **발행용 DSN**(`BACKEND_DATABASE_URL`) 추가. `packages/data-access/.../database.py`
  가 두 풀을 생성하도록.
- **엔진/세션 배선**: `services/agent-worker/app/core/database.py`,
  `services/main-server/app/core/database.py`.
- **스키마 2분할**: `database/schema.sql`/마이그레이션을 수집 DB 스키마(워커 테이블) /
  백엔드 DB 스키마(회원·관리자·결제 + 발행 산출물 테이블)로 분리. 마이그레이션 러너
  (`database/migrate.py`)에 **대상 DB 선택**(collection/backend) 도입.
- **api.* view 제거 → 발행 테이블 대체**: cross-DB JOIN 불가하므로 백엔드 DB 에 산출물 복제
  테이블 신설. 기존 `api_schema_read_contract`/`db_roles_and_grants` 는 컷오버 시 재설계.
- **워커 발행 모듈**: 워커가 작업 완료 후 백엔드 DB 로 산출물 기록(앱레벨 publisher).
- **그린필드 부트스트랩**: 두 DB용 schema/seed 적용 스크립트.

### Group C — 문서

- `docs/architecture-diagram.md`: 2-DB 토폴로지 + 발행 흐름.
- `docs/pre-deploy-staging-rehearsal-runbook.md`: 2 인스턴스 provisioning/마이그레이션 단계.

## 5. PR 분할 / 롤아웃 순서

1. **PR-0 (본 PR)**: 본 계획 문서.
2. **PR-A**: Group A (백엔드 갭 마이그레이션 + CRUD). 단일 DB 에 바로 반영 가능, 위험 낮음.
3. **PR-B**: Group B (2-DB 설정·발행·스키마 분할). 인프라(인스턴스 2대) 동반.
4. **PR-C**: Group C (문서). PR-B 와 함께/직후.

## 6. 검증

- 두 DB 에 마이그레이션 적용(로컬: 인스턴스 2개 또는 동일 인스턴스 2 DB 리허설).
- Group A: `pytest` (data-access/main-server) + 수동 — 회원 생성/소프트삭제,
  결제→환불 시 `payments` 이력 2행 보존, `/history` 가 환불 노출.
- Group B: 워커 작업 후 백엔드 DB 발행 테이블에 행 생성, 백엔드가 수집 DB 직접 의존 안 함.
- Group C: 문서 다이어그램/런북이 코드와 일치.
