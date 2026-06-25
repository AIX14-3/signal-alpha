# Signal α — 개발 가이드

루트 `README.md`, `AGENTS.md`의 개발 규칙을 한눈에 모은 문서입니다. 명령이 충돌하면 루트 문서와
실제 코드를 우선합니다.

## 의존성 관리 (uv 단일화)

**의존성의 단일 출처는 `pyproject.toml` + `uv.lock`** 입니다. 이 레포는 `requirements.txt`를 쓰지 않습니다
(Dockerfile·로컬 모두 `uv sync --locked`).

- 추가/변경: 해당 `pyproject.toml` 수정 → `uv lock` → `uv sync`
- 손으로 만든 `requirements.txt`를 다시 만들지 마세요(과거 uv.lock과 어긋나 누락 전례 있음)
- uv를 못 쓰는 외부 환경에 목록이 필요하면 그때 생성해서 전달(커밋 X):
  `uv export --package <service> --no-hashes --no-dev --no-emit-workspace`

## 설치

저장소 루트에서 워크스페이스를 동기화합니다.

```powershell
uv sync --all-packages --group dev
```

## 서비스 실행

서비스 명령은 각 서비스 디렉터리에서 실행해야 로컬 `app/` 모듈이 해석됩니다.

```powershell
cd services/main-server
uv run uvicorn app.main:app --reload

cd ../agent-worker
uv run uvicorn app.main:app --reload --port 8011
```

## 테스트

```powershell
cd services/main-server   ; uv run pytest
cd ../agent-worker        ; uv run pytest
cd ../../packages/data-access ; uv run pytest
cd ../signal-core         ; uv run pytest
```

프론트엔드:

```powershell
cd web
npm test
```

## 데이터베이스

스키마의 유일한 기준은 `database/migrations/`. 이미 적용된 migration은 수정하지 말고 새 번호로 추가합니다.

```powershell
uv run python database/migrate.py status
uv run python database/migrate.py apply
uv run python database/tools/check_schema.py
```

- 애플리케이션 코드나 임시 setup 스크립트에서 테이블을 만들지 않습니다.
- DB 문서가 명시적으로 허용하지 않는 한 migration에서 `IF NOT EXISTS`를 쓰지 않습니다.
- seed는 `ON CONFLICT` 기반으로 재실행 가능하게 만듭니다.

## 작업 완료 전 체크리스트

- 변경한 파일에 맞는 가장 좁고 유효한 테스트를 실행합니다.
- DB 스키마를 바꿨다면 migration status/apply와 schema drift 검사를 실행합니다.
- 구현 동작이 바뀌면 관련 문서(`docs/`, spec)도 함께 업데이트합니다.
- 문서와 코드가 맞지 않는 부분을 발견하면 조용히 넘기지 말고 최종 응답에 명시합니다.
- 사용자-facing 문구·프롬프트는 [overview.md](./overview.md)의 제품 문구 가드레일을 지킵니다.

> 서비스 경계와 아키텍처는 [architecture.md](./architecture.md), 데이터 흐름은
> [data-pipeline.md](./data-pipeline.md)를 참고하세요.
