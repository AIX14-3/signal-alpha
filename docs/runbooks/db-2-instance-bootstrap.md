# 런북: DB 2-인스턴스 부트스트랩 (수집 / 백엔드)

> #525/#531 WS-A 스키마 2분할. 워커 "수집 DB" 와 백엔드 "서비스 DB" 를 **물리적으로 분리된
> Postgres 인스턴스 2개**(예: Neon 2개)로 그린필드 부트스트랩한다.

## 토폴로지

| DB | 인스턴스 | 보유 테이블 | 적용 방법 |
|---|---|---|---|
| **수집(워커)** | 기존 Neon | 수집 raw + 워커 파이프라인/분석 + 발행 산출물(워커가 기록) | `migrate.py apply` (기존 그대로) |
| **백엔드(서비스)** | 신규 Neon | 회원·세션·구독·결제·관리자·약관 + 유저 콘텐츠 + **발행 산출물 사본** | `split_schema.py bootstrap-backend` |

테이블 분류는 `database/db_partition.py` 가 단일 출처:
- **BACKEND** (백엔드 DB 에만): users·user_sessions·social_accounts·subscription_plans·
  signal_subscriptions·portone_verifications·payments·admin_*·terms_agreements·watchlists·
  signal_journals·user_signal_reads·report_issuances.
- **PUBLISHED** (양쪽 DB): stocks·final_signals·analysis_results·agent_results·signal_events·
  source_documents — 백엔드 read-model(`api.signals_current`/`api.signal_detail`)이 JOIN 하는 집합.
- **COLLECTION** (수집 DB 에만): 그 외 전부. 명시 열거하지 않고 부트스트랩이 DB introspect 로 도출.

## 사전 준비

`.env` 에 두 인스턴스 DSN:
```
DATABASE_URL=postgresql://…/collection?sslmode=require            # 수집(기존)
MIGRATE_DATABASE_URL=…                                            # 수집 마이그(owner)
BACKEND_DATABASE_URL=postgresql://…/backend?sslmode=require       # 백엔드(신규) — 서비스 접속
BACKEND_MIGRATE_DATABASE_URL=…                                    # 백엔드 마이그(owner)
```
`migrate.py`/`split_schema.py` 는 `--target backend` 시 `BACKEND_MIGRATE_DATABASE_URL` →
`BACKEND_DATABASE_URL` 순으로 자동 해석한다(`--database-url` 로 직접 지정도 가능).

## 절차

### 1) 수집 DB (기존 — 변경 없음)
```bash
uv run python database/migrate.py apply --seeds          # 기존 운영 그대로
```

### 2) 백엔드 DB (신규 — 그린필드)
```bash
# 계획 미리보기(빈 DB 에선 적용/제거 수가 0 으로 보일 수 있음 — 실제 적용으로 확인)
uv run --with psycopg2-binary python database/split_schema.py bootstrap-backend --dry-run

# 실제 부트스트랩: 전체 마이그 적용 → 수집 전용 테이블 DROP CASCADE → 백엔드 시드
uv run --with psycopg2-binary python database/split_schema.py bootstrap-backend
```
동작:
1. 전체 마이그레이션 적용(테스트된 DDL) → 정확한 스키마 + `schema_migrations` 원장 풀 적재.
2. `db_partition.collection_only(actual)` 만 `DROP TABLE … CASCADE` — 백엔드/발행 테이블에서
   수집 테이블로 향하는 cross-DB FK 도 CASCADE 가 함께 제거.
3. 시드는 관용 적용 — 사라진 수집 테이블 대상 시드(dart_corp_codes 등)는 skip,
   `subscription_plans`·`stocks` 만 적재.

이후 백엔드 전용 신규 마이그는 `migrate.py new "…" --target backend` 로 만들고
`migrate.py apply --target backend` 로 적용한다(원장이 이미 차 있어 기존 마이그는 재적용 안 함).

## 검증

부트스트랩 후 백엔드 DB 에서:
- 보존 테이블 = BACKEND(15) ∪ PUBLISHED(6) ∪ `schema_migrations` = **22개**.
- **dangling FK 0** (존재하지 않는 테이블 참조 FK 없음 — cross-DB FK 정리 완료).
- `final_signals` 에 return 채널 컬럼(`ml_final_score/ml_direction/ml_confidence`) 존재.
- `api.signals_current` / `api.signal_detail` / `api.stocks` 조회 가능(read-model self-contained).
- `subscription_plans`(2) · `stocks` 시드 적재.

재시도(그린필드 리셋)가 필요하면:
```sql
DROP SCHEMA IF EXISTS api CASCADE;
DROP SCHEMA public CASCADE; CREATE SCHEMA public;
```
후 2) 재실행.

## 컷오버 (#11 — 배선 완료, 배포 토글)

런타임 컷오버는 **코드 변경 없이** 환경변수 토글이다:

1. **main-server → 백엔드 DB**: `docker-compose.yml` 이 이미 main-server 의
   `DATABASE_URL: ${BACKEND_DATABASE_URL:-<owner fallback>}` 로 와이어링돼 있다.
   `BACKEND_DATABASE_URL` 을 채우면(.env) main-server 는 백엔드 DB 로 접속한다.
   비-compose 배포(Cloud Run 등)는 main-server 서비스의 `DATABASE_URL` 을 백엔드 인스턴스로
   직접 지정한다. (검증: 백엔드 DB 가 plans 조회·api.signals_current 조회·users 쓰기 모두 정상.)
2. **워커 → 백엔드 발행**: `BACKEND_DATABASE_URL` 설정 시 AGGREGATE 가 발행분에 한해
   `PUBLISH_SIGNALS` 를 인큐 → `publish_stock` 이 PUBLISHED 6테이블을 백엔드로 복사(멱등).
   미설정이면 단일 DB 모드(발행 no-op).

**그린필드 주의**: 백엔드 DB 는 회원/구독 데이터를 백필하지 않는다(신규 부트스트랩). 컷오버 즉시
기존 단일 DB 의 회원은 백엔드 DB 에 없으므로 **신규 가입부터 시작**한다(의도된 그린필드). 또한
PUBLISHED 테이블은 워커가 발행하기 전까지 비어 있어 `api.*` 가 0행 → 종목별 발행이 누적되며 채워진다.

3. **단일 DB `api.*` 읽기 계약 정리**: 컷오버 완료 후 수집 DB 의 `api.*` view/`signal_backend`
   grant 는 더 이상 쓰지 않으므로 별도 마이그레이션으로 정리(선택).
