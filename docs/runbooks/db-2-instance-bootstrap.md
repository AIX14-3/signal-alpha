# 런북: DB 2-인스턴스 부트스트랩 (수집 / 백엔드)

> 수집(워커) DB 와 백엔드(서비스) DB 를 **물리적으로 분리된 Postgres 인스턴스 2개**(배포 전: Neon 2채널,
> 운영: Cloud SQL ×2)로 부트스트랩한다. 마이그/시드는 `-- target:` 으로 대상 DB 를 가린다.
> 분류 규칙: [database/docs/migration_seed_targets.md](../../database/docs/migration_seed_targets.md).

## 토폴로지

| DB | 인스턴스 | 보유 | 적용 |
|---|---|---|---|
| **수집(워커)** | 수집 Neon/Cloud SQL | COLLECTION + PUBLISHED + 인프라(롤/api view) | `migrate.py apply --target collection --seeds` |
| **백엔드(서비스)** | 백엔드 Neon/Cloud SQL | BACKEND + PUBLISHED(발행 사본) + 인프라 | `migrate.py apply --target backend --seeds` |

- 더 이상 "전부 적용 후 수집전용 DROP CASCADE"(구 `split_schema.py`) 트림이 필요 없다 — 마이그가
  타깃별로 분리됐다. `split_schema.py` 는 은퇴(에러 스텁).
- cross-DB FK 는 불가하므로 publisher(수집→백엔드)가 정합성을 담당한다.

## 사전 준비

루트 `.env` 에 두 인스턴스 DSN:
```
DATABASE_URL=postgresql://…/collection?sslmode=require            # 수집(워커 접속)
MIGRATE_DATABASE_URL=…                                            # 수집 마이그(owner)  (없으면 DATABASE_URL)
BACKEND_DATABASE_URL=postgresql://…/backend?sslmode=require       # 백엔드(서비스 접속 + publish 대상)
BACKEND_MIGRATE_DATABASE_URL=…                                    # 백엔드 마이그(owner)
```
`migrate.py`/`rebaseline.py` 는 `--target backend` 시 `BACKEND_MIGRATE_DATABASE_URL` →
`BACKEND_DATABASE_URL` 순으로 해석한다(`--database-url` 직접 지정도 가능).

## 0) 표 baseline 생성 (최초 1회 / 스키마 변경 후)

표 DDL baseline(`0002/0003/0004`)은 **현재 스키마를 떠서** 타깃별로 분해한다(손으로 작성 금지 →
드리프트 방지). 정적 baseline(`0001/0005/0005b/0006/0007`)은 이미 커밋돼 있다.

```bash
# 미리보기: 객체→타깃 분류 + 제거될 cross-DB FK 요약(파일 안 씀)
uv run --with psycopg2-binary python database/rebaseline.py --source-url <전체 마이그 적용된 정상 DB DSN>

# 실제 생성: 0002/0003/0004 작성 + 레거시 마이그 → migrations/archive/ 이동
uv run --with psycopg2-binary python database/rebaseline.py --source-url <DSN> --apply
```
> `--source-url` 은 **레거시 38개 마이그가 전부 적용된** 알려진-정상 DB(단일 DB 또는 기존 수집 DB).
> pg_dump 로 전체 public 스키마를 받아 타입/함수/트리거까지 정확히 보존한다. **미리보기 분류를 먼저
> 검토**하고 `--apply` 한다.

## 1) 수집 DB (그린필드 리셋 후 적용)

```bash
# 그린필드 리셋(기존 단일/수집 DB 를 깨끗이)
psql "$MIGRATE_DATABASE_URL" -c "DROP SCHEMA IF EXISTS api CASCADE; DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
# 적용: collection + all 마이그/시드만
uv run python database/migrate.py apply --target collection --seeds
```

## 2) 백엔드 DB (그린필드)

```bash
psql "$BACKEND_MIGRATE_DATABASE_URL" -c "DROP SCHEMA IF EXISTS api CASCADE; DROP SCHEMA public CASCADE; CREATE SCHEMA public;"
# 적용: backend + all 마이그/시드만 (collection 마이그는 적용 안 됨)
uv run python database/migrate.py apply --target backend --seeds
```

## 검증

```bash
python database/tools/check_targets.py                       # 모든 마이그/시드 -- target: 명시(드리프트 가드)
uv run python database/migrate.py status --target collection  # collection+all 만 표시
uv run python database/migrate.py status --target backend     # backend+all 만 표시
```
- **수집 DB**: 백엔드 테이블(users/admin/payments/…) **부재**, 수집+발행 테이블 존재,
  `api.signals_current`·`api.analysis_pipeline_status` 조회 가능.
- **백엔드 DB**: 보존 = BACKEND(15) + PUBLISHED(6) + `schema_migrations`. collection 테이블 **부재**,
  dangling FK 0, `final_signals` return 채널 컬럼(ml_final_score/ml_direction/ml_confidence) 존재,
  `api.signals_current`/`api.signal_detail` 조회 가능, `subscription_plans`(2)·`stocks` 시드 적재.

## 컷오버 (#11 — 배선 완료, 배포 토글)

런타임 컷오버는 **코드 변경 없이** 환경변수 토글이다:

1. **main-server → 백엔드 DB**: `docker-compose.yml` 이 main-server 의
   `DATABASE_URL: ${BACKEND_DATABASE_URL:-…}` 로 와이어링됨. 비-compose(Cloud Run 등)는 main-server
   `DATABASE_URL` 을 백엔드 인스턴스로 직접 지정.
2. **워커 → 백엔드 발행**: `BACKEND_DATABASE_URL` 설정 시 AGGREGATE 가 발행분에 한해 `PUBLISH_SIGNALS`
   인큐 → `publish_stock` 이 PUBLISHED 6테이블을 백엔드로 멱등 복사. 미설정이면 단일 DB 모드(발행 no-op).

**그린필드 주의**: 백엔드 DB 는 회원/구독을 백필하지 않는다(신규 부트스트랩). 컷오버 즉시 신규 가입부터
시작한다. PUBLISHED 테이블도 워커가 발행하기 전까지 비어 `api.*` 가 0행 → 종목별 발행으로 채워진다.
