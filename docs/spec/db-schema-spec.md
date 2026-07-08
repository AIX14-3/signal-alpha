# Signal Alpha DB 스키마 설계 (신규 기획)

> 기준일: 2026-06-24
> 대상: `database/migrations`, `database/seeds`, `packages/data-access`
> 목적: 신규 제품 기획(포트원 본인인증·단일 구독·무제한 관심종목·리포트 전체 공개)에 필요한 DB 스키마를 확정한다. 백엔드/프론트가 이 문서를 데이터 정본으로 참조한다.
> 연관 문서: [main-server-api-spec.md](./main-server-api-spec.md), [web-frontend-spec.md](./web-frontend-spec.md), [web-frontend-design.md](./web-frontend-design.md)

---

## 1. 범위와 원칙

- 신규 기획은 **인증/회원/결제/리포트 열람** 영역의 스키마를 바꾼다. 데이터 수집·분석(L1~L10) 스키마는 변경하지 않고 **읽기**로만 사용한다.
- baseline(`001_baseline.sql`)에 **이미 준비된 테이블**(포트원/소셜/약관/구독/관리자/세션)을 최대한 그대로 활용하고, 부족한 부분만 신규 마이그레이션으로 더한다.
- 마이그레이션은 적용 후 **불변**이며 체크섬 원장(`schema_migrations`)으로 검증된다. 신규 변경은 항상 새 파일로 추가한다.

---

## 2. 마이그레이션 도구 규칙 (이 브랜치 기준)

- `database/migrate.py` 명령은 `status`, `apply` 두 가지뿐이다. **`new`/타임스탬프 생성 서브커맨드는 없다.**
- 적용 순서는 `migrations/*.sql` 파일명 정렬. **main 최신은 `024_datalab_keyword_review_status.sql`**(분기 이후 main 이 019~024 추가).
- 따라서 신규 마이그레이션은 main 충돌을 피해 **정수 순번 `025_`, `026_`, `027_`** 로 작성한다. (프로젝트 메모리의 "타임스탬프 파일명" 규칙은 이 브랜치 `migrate.py` 에 미반영 — 정수 순번 충돌이 반복되면 타임스탬프 전환 검토.)
- 시드(`seeds/*.sql`)는 원장에 기록되지 않으므로 **`ON CONFLICT` 멱등**이어야 한다.
- 적용 전 PG16 로컬에서 `python database/migrate.py apply --dry-run` → `apply` 순으로 검증한다. SQL 은 **LF 개행**으로 저장한다(CRLF 시 체크섬 깨짐, `.gitattributes` 확인).

---

## 3. 신규 마이그레이션 (이번 추가)

| 파일 | 변경 | 핵심 |
|---|---|---|
| `025_users_phone.sql` | `users.phone VARCHAR(20)` 추가 + partial unique | 본인인증 핸드폰 = 활성 사용자 유니크. 탈퇴 후 재가입 허용 |
| `027_subscription_single_product.sql` | 플랜 정리 | 단일 상품 `monthly_9900` upsert, `free` 관심종목 무제한, `pro`/`premium` 비활성 |

> ~~`026_report_issuances.sql`~~ **descoped**: 리포트 열람 쿼터/언락 모델을 도입하지 않기로 확정(2026-07-07, 비회원 블라인드 제거·리포트 전체 공개). 해당 테이블은 만들지 않는다.

### 3.1 `users.phone`

```sql
ALTER TABLE users ADD COLUMN IF NOT EXISTS phone VARCHAR(20);

CREATE UNIQUE INDEX IF NOT EXISTS uq_users_phone_active
    ON users (phone)
    WHERE phone IS NOT NULL AND deleted_at IS NULL;
```

- `phone` 은 포트원 본인인증 성공 시 저장. 미인증/관리자 행 대비 NULL 허용.
- 활성(미탈퇴) 사용자 범위에서만 UNIQUE → 같은 사람의 탈퇴 후 재가입 가능.
- ⚠️ **정규화 필수(앱 레이어)**: 유니크는 저장 형식이 일관될 때만 유효하다. 백엔드는 저장 전 핸드폰을 단일 형식(숫자만, 예 `01012345678`)으로 정규화해야 한다. 형식이 섞이면(`010-1234-5678` vs `01012345678`) 중복 가입이 우회된다.

### 3.2 ~~`report_issuances`~~ (descoped)

리포트 열람 쿼터/언락 모델은 **도입하지 않는다**(2026-07-07 확정). 리포트는 비로그인 포함 전체 공개이므로 열람을 카운트할 테이블이 필요 없다. 구독은 저널 등 쓰기 전용 기능만 게이팅한다.

### 3.3 구독 플랜 정리 (`subscription_plans`)

| plan_type | 상태 | price_monthly | max_watchlist | 비고 |
|---|---|---:|---:|---|
| `free` | active | 0 | 무제한(2147483647) | 비구독 회원 기본. 리포트 전체 공개(열람 제한 없음) |
| `monthly_9900` | active | 9,900 | 무제한 | 단일 구독 상품. 저널 등 구독 전용 기능 |
| `pro` | **inactive** | 9,900 | 20 | 구 모델 비활성(행 보존) |
| `premium` | **inactive** | 19,900 | 100 | 구 모델 비활성(행 보존) |

- `max_watchlist` 는 `INTEGER NOT NULL` 이라 "무제한"을 INT 최대값으로 표기. **백엔드는 관심종목 한도 검사를 하지 않는 것이 정본**이며 이 값은 표시/하위호환 상한일 뿐이다.
- ⚠️ **시드/마이그레이션 순서**: `migrate.py` 는 마이그레이션 적용 후 시드를 실행한다. fresh DB 에서는 `027` 의 UPDATE 가 빈 테이블을 만나 no-op 이 되므로, **시드(`seeds/002_seed_subscription_plans.sql`)가 신규 모델(free 무제한 + monthly_9900, pro/premium 미시드)의 정본**이어야 한다. 기존 DB 는 시드 `ON CONFLICT DO NOTHING` 으로 보존되고 `027` 마이그레이션이 전환한다. (시드 파일을 신규 모델로 갱신 완료.)

---

## 4. 재사용 테이블 (baseline 그대로, 변경 없음)

신규 기획이 의존하지만 **스키마 변경이 필요 없는** 테이블. 백엔드가 라우트/리포지토리만 신규 구현한다.

| 테이블 | 용도 | 신규 기획 매핑 |
|---|---|---|
| `users` | 회원 | `member_code`(영문4+숫자4), `phone`(신규), `password_hash`=사용자 미사용 |
| `user_sessions` | refresh 세션 | 본인인증/소셜 로그인 후 자체 발급 refresh 해시 저장 |
| `social_accounts` | 소셜 연동 | `(provider, provider_user_id)` 유니크. 연동=upsert, 해제=토큰 삭제/행 제거 |
| `portone_verifications` | 포트원 | `verification_type='identity'`(본인인증) / `'payment'`(결제). `imp_uid`/`merchant_uid`/`raw_response` |
| `terms_agreements` | 약관 동의 | 가입 시 서비스/개인정보/위험고지 동의 기록 |
| `signal_subscriptions` | 구독 | active 부분 유니크(1인 1활성). 결제 검증 후 active·30일 만료 생성 |
| `subscription_plans` | 플랜 | §3.3 로 시드 정리 |
| `admin_accounts` / `admin_sessions` | 관리자 | 하드코딩 계정 로그인/세션. 회원가입 없음 |
| `watchlists` | 관심종목 | `(user_id, stock_id)` 유니크. **한도 검사 제거(무제한)** |
| `signal_journals` | 저널 | **구독 전용**. `final_signal_id` + 작성 시점 스냅샷(`signal_score_at_time` 등) + `user_view`(watch/research_more/not_relevant) + `user_memo` + `tags` |
| `signal_journal_outcomes` | 저널 결과 추적 | 작성 후 7/30 거래일 주가 변동 확정(`horizon`/`base_price`/`outcome_price`/`change_pct`). 워커 러너 기록, `(journal_id, horizon)` 유니크 |
| `user_signal_reads` | 읽음 | 상세 진입 읽음 기록 |
| `final_signals` | 리포트 본문 | `is_current`/`run_key`/`signal_date`/`score_breakdown`/`summary`/`positive_evidence`/`caution_evidence` |
| 원천 raw 테이블 | 소스 상세 | `dart_raw_details`, `report_raw_details`, `hiring_raw_details`, `datalab_raw_details`(+`datalab_category_stocks`), `stocks`/`ohlcv_data`/`price_snapshots`/`fundamentals` |

---

## 5. member_code 생성 규칙 (영문4 + 숫자4)

- 형식: 대문자 영문 4 + 숫자 4 = **8자**(예: `ABCD1234`). `users.member_code VARCHAR(20) UNIQUE` 컬럼에 저장.
- 생성: 회원가입(본인인증 성공) 시 백엔드가 무작위 생성 후 유니크 충돌 시 재시도. 핸드폰 의존성 분산을 위해 phone 과 독립적으로 부여한다.
- 혼동 문자(0/O, 1/I 등) 제외 권장(가독성). 상세 규칙은 [main-server-api-spec.md](./main-server-api-spec.md) §3.

---

## 6. 리포트 5소스 ↔ 원천 테이블 매핑 (소스 상세 페이지용)

| 소스(연결점) | score_breakdown 키 | 원천 테이블 | 종목 상세 조회 키 |
|---|---|---|---|
| 주식정보(price) | `PRICE` | `stocks`/`ohlcv_data`/`price_snapshots`/`fundamentals` | `stock_id` |
| DART(dart) | `DART` | `dart_raw_details` + `raw_documents` | `stock_id` |
| 채용공고(hiring) | `ALTERNATIVE.hiring` | `hiring_raw_details` + `raw_documents` | `stock_id` |
| 네이버키워드(datalab) | `ALTERNATIVE.datalab` | `datalab_raw_details` + `datalab_category_stocks` | `category_id → stock_id` |
| 증권사리포트(report) | `REPORT` | `report_raw_details` + `raw_documents` | `stock_id` |

- `ALTERNATIVE.patent` 는 5개 연결점에서 **제외**(API 레벨 필터). DB 는 그대로 둔다.
- **리포트 전체 공개**: 모든 소스·종합점수·LLM요약을 비로그인 포함 누구나 열람(비회원 블라인드 제거, 2026-07-07).

---

## 7. 적용 순서·검증

1. `python database/migrate.py status` — 018 까지 적용 확인.
2. `python database/migrate.py apply --dry-run --seeds` — 025/027 대상 확인(026 descoped).
3. `python database/migrate.py apply --seeds` — 적용.
4. 검증 쿼리: `users.phone` 인덱스 존재, `subscription_plans` 에서 `is_active=TRUE` 가 `free`/`monthly_9900` 2종.
5. 드리프트 주의(메모리): 일부 환경은 마이그레이션 드리프트가 있으므로 운영 DB 적용 전 스키마 비교.
