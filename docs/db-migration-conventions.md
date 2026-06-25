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

## 기능 제거 = forward-only drop
머지된 기능 제거 시 과거 마이그레이션을 수정하지 않고 **새 drop 마이그레이션**을 추가한다
(예: `012_drop_sec_filings.sql`). 이번 임베딩/pgvector 제거도 동일:
- `20260625_1024_drop_embeddings_pgvector.sql` 가 `dart_chunks`/`dart_document_features`/
  `report_chunks` 3개 테이블과 `vector` 확장을 드롭.
- 과거 `001`/`021`/`022` 의 `CREATE EXTENSION vector` + vector 컬럼은 보존 → fresh DB 적용 시
  생성됐다가 마지막 drop 에서 제거되어 **최종 스키마는 pgvector-free**.
- GCP Cloud SQL 은 pgvector 를 지원하므로 배포 시 `CREATE EXTENSION` 단계가 통과한다.
  로컬 docker 는 같은 이유로 `pgvector/pgvector:pg16` 이미지를 유지.

## 검증 결과(이 브랜치)
- 전체 마이그레이션(29) + 시드(7) fresh 적용 성공.
- `check_schema.py` → **드리프트 없음**(CREATE EXTENSION → … → DROP EXTENSION 전체 시퀀스
  무결성 확인).
- 임베딩 제거 후 테이블 **68 → 65**, `vector` 타입/확장/인덱스 0.

## (선택) 완전 pgvector 제거 변형
로컬 이미지까지 `postgres:16`(pgvector 없음)으로 가려면 과거 마이그레이션에서 vector 흔적을
직접 제거해야 한다 → 기존 DB 체크섬 리셋(재생성) 필요. 배포 전 단계라 가능하지만, 본 PR 은
무위험 forward-only 를 택했다. 필요 시 별도 작업으로 진행.
