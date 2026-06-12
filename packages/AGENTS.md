# AGENTS.md

이 디렉터리는 서비스들이 공유하는 Python 패키지를 담습니다.

## 패키지 경계

- `packages/data-access`는 재사용 가능한 repository helper와 DB 접근 패턴을 담당합니다.
- `packages/signal-core`는 공통 안전 검사, 계약 helper, 도메인 중립 로직을 담당합니다.

서비스별 orchestration, FastAPI route, collector, analyzer를 shared package에 넣지 마세요.

## data-access

- 서비스에 SQL을 중복 작성하기보다 repository method를 우선 사용하세요.
- method는 하나의 테이블 그룹 또는 workflow에 집중하게 유지하세요.
- 기획 문서가 아니라 실제 migration schema에 맞추세요.
- SQL 구조, parameter, 기대 repository 동작에 대한 테스트를 추가하세요.
- 투자 문구 판단을 이 계층에 넣지 마세요. repository는 데이터를 저장하고 조회하는 책임만 가집니다.

## signal-core

- 안전/검증 로직은 가능한 한 결정적으로 유지하세요.
- 금지 표현 필터는 매수/매도/보유 추천 문구 금지라는 제품 규칙을 지원해야 합니다.
- 공유 contract는 작고 안정적으로 유지하세요.

## 테스트

각 패키지 디렉터리에서 실행합니다.

```powershell
uv run pytest
```
