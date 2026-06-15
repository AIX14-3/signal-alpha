# 마이그레이션 규칙 (협업 가이드)

이 문서는 마이그레이션을 **단일 베이스라인으로 통합한 배경**과, 앞으로 팀이
충돌 없이 협업하기 위한 **운영 규칙 + PR 규칙**을 정의한다.
스키마 정의의 유일한 기준은 항상 `database/migrations/`이다.

---

## 1. 무엇을 바꿨나 (베이스라인 통합)

개발 단계라 운영 데이터가 없으므로, 그동안 누적된 `001`~`021` 21개 마이그레이션을
**단일 `001_baseline.sql` 한 파일로 squash** 했다.

| 항목 | 처리 |
| --- | --- |
| ALTER류 (014/017/018/019/021) | 대상 `CREATE TABLE`에 컬럼·인덱스로 **흡수** (ALTER 제거). 014→`hiring_raw_details.observed_date`, 017→`final_signals`(consensus_score/positive_evidence/caution_evidence), 018→`datalab_category_keywords.polarity`, 019→`patent_raw_details`(llm_features/llm_status+부분인덱스), 021→`hiring_signals.calculation_phase` |
| 신규 테이블 (015/016/020) | `hiring_signals`, `hiring_sources`, `hiring_job_functions`, `hiring_job_function_stocks`를 베이스라인에 합침. `IF NOT EXISTS`는 컨벤션(§3)대로 제거 |
| 016 종목별 크롤러 INSERT 15건 | `seeds/005_seed_hiring_sources.sql`로 **분리** (시드는 마이그레이션에 넣지 않음) |
| 013 레거시 `report_raw` / `report_signal` | report 파이프라인 코드가 아직 사용 중이라 **보존**. 베이스라인 맨 아래 Legacy 섹션 (폐기 예정, 신규 참조 금지) |
| `schema_migrations` 원장 | 러너가 자동 관리. 베이스라인에 포함하지 않음 |

결과: 마이그레이션 파일 **1개**(`001_baseline.sql`) + 시드 5개. 총 **52개 테이블**.

**컬럼 순서·제약 이름까지 원본과 일치**시켜, 베이스라인으로 만든 스키마가 구 21개
순차적용 결과와 **pg_dump 기준 byte-identical**임을 검증했다 (folded 컬럼은 순차적용의
ALTER append 위치인 테이블 끝에 배치, `hiring_signals`/`hiring_sources`의 UNIQUE는
인라인 자동이름 유지).

> ⚠️ ENUM 타입 `hiring_crawler_type`은 기존 016에서 도입된 것이라 스키마 동등성을 위해
> 그대로 유지했다. 컨벤션(§3 "ENUM성 컬럼은 VARCHAR + CHECK")과는 어긋나므로,
> 후속 정리 대상이다 (지금 바꾸면 스키마가 달라져 통합 검증이 깨짐).

---

## 2. ⚠️ 이 PR을 받은 모든 팀원이 해야 할 일

베이스라인 통합은 **기존에 적용된 마이그레이션 파일명을 삭제**한다. 러너는 checksum
원장(`schema_migrations`)으로 "원장에 있는데 파일이 없는 경우"를 **에러로 차단**한다
(`migrate.py`의 `verify_applied`). 따라서 기존 dev DB는 그대로 두면 `migrate apply`가
실패한다.

**해결: dev DB를 재생성한다.**

```bash
docker compose down -v          # 볼륨까지 삭제 (원장 초기화)
docker compose up -d postgres
docker compose run --rm migrate # 001_baseline.sql + seeds 재적용
```

운영/스테이징 DB에는 적용하지 않는다 (개발 단계 한정 작업).

---

## 3. 앞으로의 규칙 (베이스라인 이후)

1. **`001_baseline.sql`은 freeze.** 이미 적용된 베이스라인이므로 **절대 수정 금지**
   (러너가 sha256 checksum으로 차단). 스키마를 바꾸려면 **새 번호 파일**을 추가한다.
2. 다음 변경은 `002_<짧은설명>.sql`부터 **증분(incremental) 마이그레이션**으로 추가한다.
   - 1 논리적 변경 = 1 파일. 무관한 변경을 한 파일에 섞지 않는다.
   - `IF NOT EXISTS` 금지. 명명 규칙(`uq_`/`idx_`/`trg_`/`chk_`)·`TIMESTAMPTZ`·
     `updated_at` 트리거 등 `README.md` §3 컨벤션을 따른다.
3. **다음 squash는 언제?** 운영 데이터가 생기기 전(MVP 출시 전)까지만 베이스라인
   재통합을 허용한다. 출시 후에는 증분 마이그레이션만 추가하고 squash하지 않는다.
4. 시드 데이터는 마이그레이션이 아니라 `seeds/NNN_*.sql`에 두고 `ON CONFLICT`로
   idempotent하게 작성한다.

---

## 4. PR 규칙 (DB 변경이 포함된 PR)

DB 변경 PR은 아래를 **모두** 만족해야 리뷰를 요청한다.

**작성자 체크리스트**

- [ ] `database/migrations/`에 **다음 번호** 파일로 추가했다 (기존 파일 수정·삭제·리네임 안 함).
- [ ] 새 테이블이면 `README.md` §2 인벤토리 + `erd/signal_alpha_core_erd.md` +
      `docs/table_descriptions.md` 세 곳을 모두 갱신했다.
- [ ] 컨벤션(§3) 준수: plain `CREATE TABLE`, `TIMESTAMPTZ`, 명명 규칙,
      `updated_at` 트리거, ENUM성 컬럼은 `VARCHAR + CHECK`.
- [ ] 시드는 `seeds/`에 분리하고 `ON CONFLICT`로 재실행 안전하게 작성했다.
- [ ] 로컬에서 적용 + 드리프트 검증을 통과했다:
      ```bash
      uv run python database/migrate.py apply
      uv run python database/tools/check_schema.py   # exit 0 이어야 함
      ```
- [ ] PR 본문에 변경 요약과 "재생성 필요 여부"를 명시했다.

**리뷰어 체크리스트**

- [ ] 번호 충돌 없음 (동시 PR이 같은 번호를 쓰지 않음 — 늦게 머지되는 쪽이 리넘버).
- [ ] 베이스라인/적용된 파일을 수정하지 않았는지 확인.
- [ ] 문서 3종 동기화 확인.

**번호 충돌 처리:** 두 PR이 같은 번호를 쓰면, 나중에 머지되는 쪽이 리넘버한다.
이미 로컬 적용했다면 dev DB를 재생성하거나 원장에서 구 파일명을 삭제 후 재적용한다.

---

## 5. 관련 문서

- `database/README.md` — 러너 사용법, Zone 구조, 컨벤션(§3), 추가 절차(§4)
- `database/erd/signal_alpha_core_erd.md` — ERD (관계·컬럼)
- `database/docs/table_descriptions.md` — 테이블별 역할 한 줄 설명
