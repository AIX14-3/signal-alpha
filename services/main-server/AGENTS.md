# AGENTS.md

이 디렉터리는 사용자-facing FastAPI 서비스입니다. 프론트엔드와 향후 외부 클라이언트가 사용하는 API 경계입니다.

## 경계

- 수집, 크롤링, LLM, RAG, 키움 로직을 `main-server`에 넣지 마세요.
- 읽기/쓰기에는 `packages/data-access`의 DB repository를 사용하세요.
- 분석은 저장된 결과를 읽거나 내부 worker API를 명시적으로 호출하는 방식으로 연결하세요.
- collector, analyzer, queue, 최종 signal 생성의 소유자는 `agent-worker`입니다.

## API 원칙

- 프론트엔드-facing 조회는 `final_signals`와 관련 근거 테이블을 우선 사용합니다.
- 관심종목, 분석 요청, signal 상세 조회, journal, 읽음 상태, 사용자/결제 경계는 이 서비스에 속합니다.
- API 응답이 투자 조언처럼 들리면 안 됩니다.
- 추천 표현 대신 "소스 간 일치도", "근거", "방향성", "추가 확인 필요" 개념을 사용하세요.

## 현재 상태

현재 구현된 API:

- `GET /health`
- `GET /signals/{ticker}`

계획 또는 부분 구현 영역은 기획 문서만 보고 만들지 말고 실제 스키마와 repository를 기준으로 구현하세요.

## Signal Journal

Signal Journal은 사용자 복기 도구입니다. 투자 성과를 평가하거나 플랫폼이 특정 행동을 추천했다는 인상을 주면 안 됩니다.

## 테스트

이 디렉터리에서 실행합니다.

```powershell
uv run pytest
```

새 사용자-facing 동작을 추가하면 route/repository 통합 테스트를 추가하세요.
