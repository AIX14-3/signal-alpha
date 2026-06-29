# 배포 전 스테이징 리허설 런북 (managed staging dry-run)

목표: 실배포 난항을 미리 당겨와 정복한다. 실제 managed 스테이징 DB + 실키(DART/PortOne/Gemini)로
**전체 배포를 1회 리허설**하고, 끝나면 실배포는 이 문서를 "재생"만 하면 되게 만든다.

## 0. 전제 / 시크릿 취급
- 리허설 환경: **실제 managed 스테이징**(실배포 DB와 분리된 별도 인스턴스 권장).
- 시크릿은 **절대 채팅/커밋에 노출 금지**. 로컬 `C:\Users\biop9\signal-alpha\.env.staging`(gitignore 대상)에 값을 넣고,
  각 명령은 그 파일을 읽어 실행한다. 최소 키:
  ```
  DATABASE_URL=postgresql://USER:PW@HOST:5432/DB   # managed staging
  DB_SSLMODE=require                                # managed = require (PR#443 노브)
  DART_API_KEY=...
  PORTONE_API_SECRET=...   PORTONE_STORE_ID=...   PORTONE_CHANNEL_KEY_*=...
  GEMINI_API_KEY=...       SYNTHESIS_USE_LLM=true   SYNTHESIS_LLM_MODEL=gemini-2.0-flash
  AUTH_SECRET_KEY=<staging 전용 강한 랜덤>           # dev 기본값 금지
  ```
- 로컬에 psql/pg_dump 없음 → pg 클라이언트는 Docker(`pgvector/pgvector:pg16`)로 실행.

## Phase 1 — DB 토대 (가장 먼저, 모든 게 의존) ⭐
1. 빈 스테이징 DB에 마이그레이션 + 시드 적용:
   ```bash
   set -a; . ./.env.staging; set +a
   python database/migrate.py apply --seeds
   python database/migrate.py status        # 29 migrations 적용 확인
   ```
2. **드리프트 검증**(migrations ↔ 실제 DB 일치):
   ```bash
   python database/tools/check_schema.py    # exit 0 = zero drift
   ```
   - 임시 비교 DB를 같은 서버에 만들므로 staging 계정에 CREATE DATABASE 권한 필요.
     불가하면 로컬 docker PG를 기준으로 비교 후, staging은 status/카운트로 교차확인.
3. **권위 스냅샷** 생성(읽기용 참조, migration 원장은 그대로):
   ```bash
   docker run --rm pgvector/pgvector:pg16 pg_dump --schema-only --no-owner --no-privileges \
     "$DATABASE_URL" > database/schema.sql
   ```
4. 산출물: `database/schema.sql`, 테이블 소유권 문서(worker-write vs backend-write).
5. 정책 확정: **013 번호 충돌**(`013_dart_employee_stats` + `013_hiring_quarantine`)은 적용본 보존,
   신규는 타임스탬프 파일명(`migrate.py new`).

## Phase 2 — 마이그레이션 배포 전략 리허설
- 완전 빈 DB → `migrate.py apply --seeds`를 **init-step**으로 1회 선실행하는 패턴 확정(서비스 기동 전).
- 체크섬 원장(`schema_migrations`) 확인. 롤백은 forward-only(스테이징 DB drop→재적용) 1회 드릴.
- CRLF 함정 주의: SQL은 LF(`.gitattributes`), 체크섬 깨짐 방지.

## Phase 3 — 컴포넌트 합치기 (prod 이미지 + 실배선)

> (#11 업데이트) 운영 토폴로지는 frontend/backend/worker/collector/scheduler **5 컴퓨트 유닛 + DB 2 인스턴스**
> (수집/백엔드)다. 스테이징 리허설은 아래처럼 `agent-worker`를 **단일 통합 기동**(워커 드레인 데몬 + 가격 수집
> 내장)으로 묶어 검증한다. 유닛 분리 토폴로지는 [architecture-diagram.md](./architecture-diagram.md) 참조.
```bash
docker compose --profile prod up -d postgres   # staging은 DATABASE_URL로 대체(이 postgres 미사용)
docker compose run --rm migrate                # 또는 Phase 1에서 이미 적용
docker compose up -d agent-worker main-server
docker compose --profile prod up -d web-prod   # NEXT_PUBLIC_MAIN_API_BASE_URL build-arg!
```
- 배선 점검: `CORS_ALLOW_ORIGINS`에 프론트 도메인, `AGENT_WORKER_BASE_URL` 내부, `/health` 200(DB ok).
- 함정: `NEXT_PUBLIC_MAIN_API_BASE_URL`은 **빌드타임 인라인** — 런타임 env로는 클라이언트에 안 박힘.

## Phase 4 — 외부연동 E2E (플래그로 하나씩 격리)
1. **DART**(최대 난항): `DART_API_KEY`로 수집→정규화→분석→`final_signals.is_published=true`가
   웹에 노출되는지. 안 뜨면 화면이 빔.
2. **PortOne 실키**: 본인인증→가입→결제(성공은 검증됨)→**실 취소**(PR#444 경로) 1회.
   ⚠️ 실 취소 테스트는 실 결제가 선행돼야 함 = **실제 과금/환불 발생 가능**.
   → 가능하면 PortOne **테스트 채널/소액**으로. 본 실행 전 별도 확인.
3. **LLM synthesis**: `SYNTHESIS_USE_LLM=true`로 `python services/agent-worker/smoke_synthesis.py`
   → narrative 생성 확인(PR#445). 실패 시 결정론 폴백 확인.

## Phase 5 — 실패 모드 리허설 (진짜 고생을 당겨오기)
- DB 강제 차단 → `/health` 503(`db: error`) 확인(PR#446).
- 워커 2번째 인스턴스 기동 → advisory lock으로 가격/ops 데몬 단일화 동작 확인.
- `AUTH_SECRET_KEY` 교체 → 기존 액세스 토큰 무효화 확인.
- 롤백 드릴: 직전 이미지 태그로 되돌리기 + DB forward-only 처리 절차 기록.

## 완료 기준 (Go 체크)
- [ ] zero-drift + schema.sql 스냅샷 + 소유권 문서
- [ ] 빈 DB→마이그레이션 init-step→컴포넌트 기동 무에러(단일 통합 워커 기준)
- [ ] DART 실데이터가 웹 발행까지 도달
- [ ] PortOne 실 결제+취소 1사이클
- [ ] LLM synthesis 실키 narrative
- [ ] /health 503·워커 락·시크릿 교체·롤백 드릴 통과
- [ ] 이 런북이 실배포용으로 그대로 재생 가능
