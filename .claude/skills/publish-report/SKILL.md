---
name: publish-report
description: signal-alpha 리포트/시그널 발행. DART+corp_code 선행체크 후 수집→집계→LLM합성→백엔드 발행, 발행 충돌·재발행(target=all) 복구까지 수행. "리포트 발행", "시그널 발행", "재발행", "publish" 요청 시 사용.
allowed-tools: Bash, Read, Grep, Glob
---

# 리포트/시그널 발행

작업 디렉터리: `services/agent-worker` (모든 `uv run`은 여기서).

## 0. 선행 조건 (반드시 먼저 확인)
- `BACKEND_DATABASE_URL` 설정 여부 확인 — 미설정이면 발행은 no-op(single-DB 모드). 발행하려면 설정 필요.
- 워커 드레인 데몬이 큐를 자동소비하지 않는 환경이면 아래 스크립트로 **수동 트리거**해야 함.
- DART 인입 + corp_code sync 선행 필요. 누락 시 먼저:
  `uv run python run_collectors.py`  (COLLECT_DART + corp_code sync 포함)

## 1. 리포트 수집 (리포트 체인만, 발행 제외)
- 전체: `uv run python run_report_sample.py`
- 종목: `uv run python run_report_sample.py --ticker 005930`
- 기간: `uv run python run_report_sample.py --date-start 2025-07-01 --date-end 2025-09-30`

## 2. 발행 (집계 → LLM 합성 → 백엔드 복사)
- 당일 발행: `uv run python run_publish_current.py [--ticker 005930]`
- 큐 없이 직접 발행: `uv run python publish_one.py --ticker 005930`
- 백엔드 리셋 후 멀티소스 복구 발행: `uv run python run_recover_publish.py --ticker 005930`

## 3. 발행 충돌 / id 드리프트 시
- 증상: dead-letter, "publish target table 'X' does not exist", FK 위반.
- 진단은 `/db-check` 스킬로 위임. 구조 드리프트면 누락 백엔드 마이그 적용 후 재발행.
- 데이터 부족분은 last-known 재사용으로 폴백됨(DART/PATENT/REPORT 30일, HIRING/DATALAB/PRICE 7일).

## 4. 검증
- 백엔드 final_signals 건수 확인, 수집 DB와 대조.
- 자세한 발행 코드 흐름은 `app/publish/publish_task.py`, `app/publish/signal_publisher.py` 참조.

규칙: 점수를 임의로 뒤집지 말 것. 합산/헤드라인은 aggregation 레이어가 담당.
