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

## 컷오버 (후속 — #11)

스키마 분리는 완료지만, 런타임 컷오버는 별도(#11 물리 publisher):
- main-server `DATABASE_URL` 을 백엔드 DB 로 전환(현재는 수집 DB).
- 워커가 발행 산출물(final_signals + stocks/analysis_results/agent_results/signal_events/
  source_documents)을 백엔드 DB 로 publish(앱레벨) — 현재 백엔드의 PUBLISHED 테이블은 빈
  스키마(stocks 시드만). 발행 전까진 `api.*` 가 0행.
- 기존 단일 DB 의 `api.*` 읽기 계약은 컷오버 시 재설계.
