# DB 마이그레이션 규칙 & 스키마 스냅샷

배포(워커/백엔드/프론트/DB=GCP Cloud SQL) 전 스키마 정리 과정에서 확정한 규칙.

## 적용 도구 (새 도구 도입 없음)
- `database/migrate.py` — raw SQL 마이그레이션 적용기 + 체크섬 원장(`schema_migrations`).
  런타임은 asyncpg raw. **Alembic/ORM 미도입**(이전에 도입했다 백아웃, 재도입 금지).
- `database/tools/check_schema.py` — 드리프트 검사(임시 DB에 전체 마이그레이션을 새로
  적용해 기준 스키마를 만들고 대상 DB와 비교). 배포 전 필수.
- `database/schema.sql` — 전체 마이그레이션 적용 결과의 **권위 스냅샷**(읽기용 단일 소스,
  `pg_dump --schema-only`). 마이그레이션 원장을 대체하지 않는다.

## 명명 규칙
- **새 마이그레이션은 타임스탬프 파일명**: `python database/migrate.py new "..."` →
  `YYYYMMDD_HHMM_<name>.sql`. 정수 순번(`NNN_`)은 폐기 — 브랜치 병합 시 번호 충돌 발생.
- 이미 적용된 마이그레이션 파일은 **수정 금지**(체크섬). 변경은 항상 새 마이그레이션으로.
- SQL 파일은 **LF 개행**(`.gitattributes`). CRLF 면 체크섬이 깨진다.

## 알려진 이슈
- **013 번호 충돌**: `013_dart_employee_stats.sql` 과 `013_hiring_quarantine.sql` 가
  둘 다 존재(브랜치 충돌 흔적). `migrate.py` 는 파일명 단위로 추적하므로 둘 다 정상 적용된다
  (검증 완료). 적용본은 보존하고, 앞으로는 타임스탬프 명명으로 재발 방지.

## 기능 제거 정책
보통은 과거 마이그레이션을 수정하지 않고 **새 drop 마이그레이션**을 추가한다
(예: `012_drop_sec_filings.sql`) — 이미 배포된 DB 체크섬을 보존하기 위함.

**현재 pgvector 관련 상태:**
과거에는 배포 전 스키마 정리 과정에서 pgvector 제거 브랜치가 있었지만, 이후
`20260701_1218_agent_embeddings_pgvector.sql`로 pgvector 기반 `signal_episodes`와
비활성 `report_chunks` 스키마가 추가됐다. 따라서 현재 기준에서는 vector 확장 자체를 제거된
상태로 가정하면 안 된다.

- `signal_episodes`: 에피소드 메모리용 활성 스키마.
- `report_chunks`: 과거 Report RAG 계획에서 추가된 비활성 스키마. 현재 Report 런타임에서는
  적재하거나 조회하지 않는다.
- pgvector 관련 변경도 이미 적용된 마이그레이션을 수정하지 말고 새 타임스탬프 마이그레이션으로 처리한다.

## 검증 원칙
- 전체 마이그레이션 + 시드를 fresh DB에 적용한다.
- `database/tools/check_schema.py`로 드리프트 없음 여부를 확인한다.
- pgvector가 필요한 마이그레이션이 있으므로 검증 DB도 `vector` 확장을 지원해야 한다.
