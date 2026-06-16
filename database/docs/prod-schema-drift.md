# Prod 스키마 드리프트 findings (2026-06-16)

> 상태: **팀 조치 필요**. 작성자: C2 polarity 적용 작업 중 발견.
> 대상: 프로덕션 Supabase (`...pooler.supabase.com`).

## TL;DR

프로덕션 DB가 두 가지 면에서 마이그레이션 정의와 어긋나 있다:

1. **`schema_migrations` 원장이 비어 있다([])** — prod baseline이 이 `migrate.py`
   러너 밖(out-of-band)으로 적용됐다. 그래서 `migrate.py status`가 prod에서 **모든
   마이그를 pending으로 오표시**한다.
2. **prod baseline 자체가 스테일** — repo의 현재 `001_baseline.sql`(#108에서 001~021
   스쿼시)보다 뒤처져, 일부 컬럼이 prod에 **없다**.

이 때문에 자동화 도구를 prod에 그대로 쓰면 위험하고, **C3 특허 농축이 막혀 있다.**

---

## 어떻게 발견됐나

C2 DataLab polarity를 12종목 prod 적용(`datalab_polarity_keywords.py --apply`)하던 중
`column "polarity_source" ... does not exist`로 전부 실패 → 조사 결과 prod에 마이그
002·003이 미적용임을 확인. 이어 C3 특허 농축을 시도하자
`column "llm_status" does not exist`로 실패 → prod baseline이 019(특허 LLM 컬럼)
이전임을 확인.

## 확인된 드리프트 (증거)

읽기 전용 조회로 확인 (2026-06-16):

| 항목 | repo 정의 | prod 실제 |
| --- | --- | --- |
| `schema_migrations` 원장 | 001·002·003 기록되어야 | **빈 테이블([])** |
| 002 `set_final_signal_current` 트리거 멱등 수정 | 적용됨 | **미적용**(`version IS DISTINCT` 없는 옛 버전) |
| 003 `datalab_category_keywords` polarity provenance 5컬럼 | 존재 | **없음**(`polarity_source` 등) |
| 019 `patent_raw_details.llm_features`·`llm_status` | `001_baseline`에 흡수 | **없음** |
| public base 테이블 수 | — | 47 (baseline 골격은 존재) |

→ baseline 골격(테이블 47개)은 있으나, **#108 스쿼시 이후/직전의 일부 컬럼이 누락**.
019 외에 다른 post-squash 컬럼도 누락됐을 수 있다(전수 미확인 — 아래 한계 참고).

## 이번 세션에 prod에 적용한 변경 (기록)

C2 작업을 끝내기 위해 사용자 승인 하에 다음을 prod에 적용:

- **마이그 002·003 SQL 직접 적용** — 002는 `CREATE OR REPLACE FUNCTION`(멱등), 003은
  가산 `ADD COLUMN`(하위호환). 검증: provenance 5컬럼 생성 + 트리거에 `version IS DISTINCT`
  포함. **원장에는 기록하지 않음**(원장 백필은 "prod==baseline 미검증 단정"이라 보류).
- **C2 polarity 12종목 적용** — `datalab_categories`/`datalab_category_stocks`/
  `datalab_category_keywords`(polarity_source='llm', gemini-2.5-flash-lite). 결과 prod
  활성 15종목 전부 polarity 보유.

> 즉 prod의 002·003은 "적용됐지만 원장엔 없는" 상태다. 향후 원장 초기화 시 이 점을 반영해야
> 002·003을 중복 적용하지 않는다.

## 영향 / 리스크

- **`migrate.py apply`를 prod에 직접 쓰면 안 된다** — 원장이 비어 001을 pending으로 보고
  `001_baseline`(`CREATE TABLE`, `IF NOT EXISTS` 금지)을 재실행 → "이미 존재" 에러로 실패.
- **`database/tools/check_schema.py`를 prod에 쓰면 안 된다** — 대상 서버에
  `CREATE DATABASE/DROP DATABASE signal_alpha_schema_check`를 실행한다(매니지드 Supabase에서
  실패/위험). 로컬 Docker 전용 도구다.
- **C3 특허 농축(`run_patent_enrichment.py`) 차단** — `patent_raw_details.llm_features`/
  `llm_status`가 prod에 없어 동작 불가. 019가 스쿼시 baseline 안에 있어 **따로 적용할 마이그
  파일이 없다** → 003처럼 깔끔히 못 고친다.
- piecemeal 핸드패치(에러 날 때마다 컬럼 ALTER)는 whack-a-mole이고 거버넌스 위반
  (`database/README.md §3/§4`).

## 권장 재정합 플랜 (팀 결정)

목표: prod를 현재 `001_baseline.sql`(+ 002·003)과 정합시키고, 원장을 초기화해 이후
`migrate.py` 경로로 관리.

1. **prod 스키마를 현재 `001_baseline`과 전수 비교** — prod에 못 쓰는 `check_schema.py`
   대신, prod 덤프(`pg_dump --schema-only`)를 받아 로컬에서 `001_baseline` 적용본과 비교하거나,
   `information_schema` 기반 읽기 전용 diff 스크립트로 누락 컬럼/제약/인덱스를 전수 파악.
2. **누락분 보강 마이그 작성** — 발견된 차이를 새 `NNN_*.sql`(가산 ALTER) 한 파일로 정리.
   019 특허 컬럼은 여기 포함(`llm_features JSONB`, `llm_status` + CHECK +
   `idx_patent_llm_pending` 부분 인덱스). `001_baseline` 정의와 동일하게.
3. **원장 초기화** — prod가 `001_baseline` 골격을 가졌음이 1에서 확인되면 `001_baseline.sql`을
   applied로 기록(체크섬 포함), 이미 직접 적용한 002·003도 applied로 기록(중복 적용 방지).
   이건 prod 사실을 검증한 뒤의 의도적 백필이어야 한다.
4. **이후** `migrate.py apply`로 2의 보강 마이그를 적용 → C3 농축 unblock.

> 핵심: prod가 `migrate.py`로 관리되지 않던 갭을 닫는 일이다. 검증 없는 단정(임의 원장 백필,
> 즉석 ALTER)은 피하고, 전수 비교(1) → 보강 마이그(2) → 검증된 원장 초기화(3) 순서를 지킬 것.

## 재현용 읽기 전용 쿼리

```sql
-- 원장 상태
SELECT filename FROM schema_migrations ORDER BY filename;            -- 빈 결과 = 미초기화

-- 002 트리거 적용 여부
SELECT pg_get_functiondef('public.set_final_signal_current'::regproc) LIKE '%version IS DISTINCT%';

-- 003 polarity provenance 컬럼
SELECT column_name FROM information_schema.columns
WHERE table_schema='public' AND table_name='datalab_category_keywords'
  AND column_name LIKE 'polarity_%';

-- 019 특허 LLM 컬럼 (없으면 C3 막힘)
SELECT column_name FROM information_schema.columns
WHERE table_schema='public' AND table_name='patent_raw_details'
  AND column_name IN ('llm_features','llm_status');
```

## 한계

- prod 전수 스키마 비교는 아직 안 했다(`check_schema`가 prod에 못 쓰여서). 위 표는 C2/C3가
  실제로 부딪힌 컬럼만 확인한 것이라, **다른 누락이 더 있을 수 있다**. 재정합 플랜 1단계(전수
  비교)에서 확정해야 한다.

관련: [`migration_rules.md`](migration_rules.md), [`README.md`](../README.md),
[`table_responsibility.md`](table_responsibility.md). 메모리: `prod-migration-ledger-empty`.
