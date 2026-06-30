---
name: worker-review
description: signal-alpha agent-worker 코드 리뷰. 7영역 분리·점수 비반전·LLM합산 규칙을 적용해 변경을 검토하고 pytest+ruff 실행 후 H/M 등급으로 정리. "워커 리뷰", "worker review", 워커 코드 점검 시 사용.
allowed-tools: Bash, Read, Grep, Glob
---

# agent-worker 코드 리뷰

## 불변 규칙 (위반 시 finding)
- 7영역 분리 유지: PRICE/DART/REPORT/HIRING/PATENT/DATALAB + AGGREGATED.
- 점수를 뒤집지 않음. 합산·헤드라인은 `app/orchestrator/aggregation/tasks.py`(LLM/집계 레이어)에서만.
- `SCORING_SOURCES = {DART, HIRING, PATENT, DATALAB}`만 final_score 기여. PRICE/REPORT는 evidence.
- LLM 출력의 환각 수치 금지 — 모든 값은 DB 출처. 발행/표시 전 검증.
- 참고: `docs/worker-review-2026-06-29-fixes.md`, `docs/architecture.md`, `AGENTS.md`.

## 검토 절차
1. 변경 diff 파악 (git diff).
2. 위 규칙 + race/트랜잭션 경계/이벤트루프 블로킹 I/O/드레인 동시성 관점 점검.
3. lint: 루트에서 `uv run ruff check .`
4. test: 패키지별 실행(루트 실행 금지 — 모듈 충돌):
   - `cd services/agent-worker && uv run pytest`
   - `cd packages/data-access && uv run pytest`
   - `cd packages/signal-core && uv run pytest`
   - hiring/datalab 미구현 + 미설치 의존으로 일부 ERROR는 기대됨(코드 무결성과 분리해 보고).

## 결과 정리 (등급 체계)
- H1~H6: 치명(레이스→헤드라인 중립 고정, 블로킹 I/O, end-filter 우회 등)
- M1~M10: 중(백오프 없는 재시도, 트랜잭션 경계 부재, 비원자 dedupe, 풀 오용 등)
- Low: 파싱 오류, 데드코드, 예외 마스킹 등
각 finding은 파일:라인 + 실패 시나리오로 기술.
