# L1 정형 재무 — 구현 계획 (착수)

> 상위 스펙: `docs/spec/dart-l1-financials-spec.md`. 추적 이슈: AIX14-3/signal-alpha#136.
> 브랜치: `feat/dart-l1-financials`. 이 문서는 스펙을 **실착수 작업 단위**로 분해한다(스펙 반복 X, 시퀀스·파일·DoD 중심).

## 0. 범위 (이번 브랜치)
스펙 §6 분담 중 **🙋 본인 소유 1순위 PR**부터 착수: **`dart_financial_facts` 마이그레이션 + ORM 리포지토리(멱등 upsert)**.
수집기(`fnlttSinglAcntAll`, 🧑‍💼)·★4 파생(🧑‍💼)은 후속. 적재 배선(👥)은 리포지토리 완료 후 연결.

> 선행: 적재 스키마/리포지토리는 수집기와 독립적으로 만들 수 있으므로(SEC 002 선례와 동일), 매핑 사전 합의 전이라도 **테이블+리포지토리는 먼저 진행 가능**.

## 1. 작업 시퀀스

### Step 1 — 마이그레이션 `003_dart_financial_facts.sql` 🙋
- 위치: `database/migrations/003_dart_financial_facts.sql` (다음 가용 번호 = 003; 001_baseline, 002_sec_filings 다음).
- 내용: 스펙 §3 DDL 그대로. **`002_sec_filings.sql` 컨벤션 미러링** — BIGSERIAL PK, `stock_id BIGINT REFERENCES stocks(id)`, `fetched_at/created_at/updated_at TIMESTAMPTZ DEFAULT NOW()`, 부분 인덱스 `WHERE stock_id IS NOT NULL`.
- 자연키(멱등): `UNIQUE (corp_code, bsns_year, reprt_code, fs_div, sj_div, account_id)` — 재수집/정정 시 갱신.
- 인덱스: `(stock_id) WHERE NOT NULL`, `(corp_code, bsns_year)`, `(account_id)`.
- 주의(레포 규칙): 이미 적용된 마이그레이션 수정 금지 → `python database/migrate.py new "..."`로 **새 타임스탬프 파일에만** 추가. `IF NOT EXISTS` 사용 금지(DART 규칙). `.gitattributes`가 `*.sql eol=lf` 보장(checksum 안전).

### Step 2 — ORM 리포지토리 🙋
- 위치: `packages/data-access/signal_alpha_data_access/repositories/dart_financials.py` (신규, **`sec.py` `SecFilingRepository` 패턴 미러링**). 대안: 기존 `dart.py`에 메서드 추가 — 응집도상 신규 파일 권장.
- 클래스 `DartFinancialFactsRepository(connection)`:
  - `upsert_fact(*, corp_code, bsns_year, reprt_code, fs_div, sj_div, account_nm, ...)` — `INSERT ... ON CONFLICT (자연키) DO UPDATE SET ..., updated_at = NOW() RETURNING *` (sec `upsert_filing` 구조 그대로).
  - `upsert_facts(entries: list[dict]) -> int` — `executemany` 일괄(sec `upsert_filings` 구조). 필수키 없는 행 필터.
  - `list_by_corp_year(corp_code, bsns_year)` / `get_fact(자연키)` / `get_latest_rcept_no(corp_code, bsns_year, reprt_code)` — 정정 추적·증분 수집용.
  - 헬퍼: 금액 정규화 `_to_krw(amount_raw)`(쉼표 제거→`NUMERIC`), 기간 라벨 `_period_label(bsns_year, reprt_code)`(2025FY/2025Q3), `account_id` 빈값 폴백.

### Step 3 — 테스트 🙋
- `packages/data-access/tests/test_dart_financials_repository.py` — `test_dart_repository.py`/`test_sec_*` 패턴.
- 케이스: ① 멱등성(같은 자연키 2회 upsert → 행 수 불변, 값 갱신) ② 정정(`rcept_no` 갱신 시 최신 우선) ③ `account_id` NULL 폴백 ④ 단위 정규화(원) ⑤ 일괄 upsert 카운트.

### Step 4 — (후속, 별도) 수집기 배선
- `collectors/dart/financials_api.py`(🧑‍💼) 완료 후, 수집기 → 리포지토리 `upsert_facts` 연결. 증분(이미 적재 스킵)·정정(최신 `rcept_no`)은 스펙 §4·§5.

## 2. 착수 전 합의 필요 (스펙 §6 — 수집기/매핑 전 블로킹)
① 표준계정 매핑 범위(정규 metric 11종, 🧑‍💼) ② CFS/OFS 우선순위 ③ 분기 단일분기화 여부 ④ 신호 emit 단위.
→ **Step 1~3(테이블+리포지토리+테스트)은 위 합의와 무관하게 선행 가능.** 매핑 사전은 분석 단계 입력이라 적재 스키마를 막지 않음.

## 3. DoD
- [ ] `003_dart_financial_facts.sql` 추가, `migrate.py apply` 통과, `check_schema.py` drift 없음.
- [ ] `DartFinancialFactsRepository` 멱등 upsert·조회 동작, 실 DB(또는 테스트 컨테이너) 통과.
- [ ] 리포지토리 테스트 5케이스 green.
- [ ] 30종목 ⨯ 최근 8분기 적재 시 재수집 행 수 불변(멱등) — 수집기 배선 후 검증.

## 4. 검증 (레포 표준 명령)
```powershell
uv run python database/migrate.py status
uv run python database/migrate.py apply
uv run python database/tools/check_schema.py
cd packages/data-access; uv run pytest tests/test_dart_financials_repository.py
```

## 5. 손대지 않는 것
- 기존 `analyzers/dart/financials.py`(텍스트 정규식)는 **L1 적재 + ★4 파생이 검증될 때까지 유지**. L1이 백테스트 lift로 우위 증명 후 대체/폐기(점진 전환).
- ★6~ 이후 레이어, 수집기 본체, ★4 파생 계산은 본 브랜치 범위 밖.
