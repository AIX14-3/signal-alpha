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

**예외 — 이번 임베딩/pgvector 완전 제거(배포 전, 운영 DB 없음):**
pgvector 자체를 의존성에서 빼고 로컬도 일반 `postgres:16` 으로 가기 위해, 과거 마이그레이션을
직접 편집했다(운영 DB 부재라 체크섬 리셋 비용을 감수).
- `001_baseline.sql`: `CREATE EXTENSION vector` 와 `report_chunks` 테이블/인덱스 제거.
- `021_dart_chunks.sql`, `022_dart_document_features.sql`: 임베딩 전용 → **파일 삭제**.
- → 마이그레이션 전체에 vector 흔적 0. fresh DB 는 pgvector 없이 적용 가능.

⚠️ **체크섬 리셋 필요**: 이 브랜치를 pull 한 기존 로컬/dev DB 는 `001` 체크섬 불일치 +
삭제된 021/022 ledger 잔존으로 `migrate` 가 실패한다. **DB 를 재생성**(drop→create→
`migrate apply --seeds`)하면 깨끗하게 적용된다. 배포(GCP Cloud SQL)는 신규 인스턴스라 무관.

## 검증 결과(이 브랜치)
- 전체 마이그레이션(27) + 시드(7) 를 **일반 `postgres:16`(pgvector 미설치)** 에 fresh 적용 성공.
- `check_schema.py` → **드리프트 없음**(전체 시퀀스 무결성 확인).
- 임베딩 제거 후 테이블 **68 → 65**, `vector` 타입/확장/인덱스 0, `CREATE EXTENSION` 0.
- docker-compose 이미지 `pgvector/pgvector:pg16` → `postgres:16`.
