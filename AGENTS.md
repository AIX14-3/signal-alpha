# AGENTS.md

Signal Alpha는 데이터 방향성과 근거를 교차검증하는 모노레포 서비스입니다. 이 프로젝트는 투자 추천 서비스가 아닙니다. 이 저장소에서 작업하는 에이전트는 코드, UI 문구, 프롬프트, API, 테스트, 문서 전체에서 이 경계를 반드시 지켜야 합니다.

## 기준 문서 우선순위

문서끼리 충돌하면 아래 순서를 따릅니다.

1. 현재 브랜치의 실제 구현 코드와 테스트
2. DB 스키마는 `database/migrations/`
3. `README.md`, 서비스 README, 패키지 README
4. `docs/`의 현행 문서 (진입점은 `docs/README.md`; 개요 `docs/overview.md`, 아키텍처 `docs/architecture.md`, 데이터 흐름 `docs/data-pipeline.md`)
5. `docs/spec/*` 기술 스펙

과거 기획·리서치 사료는 `docs/archive/`에 보존되어 있으며 현행 기준이 아닙니다. 기획 문서는 목표 방향을 설명하지만 일부 내용은 구현보다 앞서 있습니다. 코드와 테스트가 뒷받침하지 않는 동작을 구현 완료로 취급하지 마세요.

## 제품 문구 원칙

Signal Alpha가 아래 기능을 제공하는 것처럼 표현하면 안 됩니다.

- 매수, 매도, 보유 추천
- 지금 사거나 팔아야 한다는 표현
- 상승 보장, 목표 수익률, 수익 예측
- 추천 종목
- 투자 타이밍 알림

대신 아래 표현을 사용합니다.

- 데이터 방향성
- 소스 간 일치도
- 근거
- 데이터 정합성
- 추가 확인 필요
- 사용자 판단 보조

사용자-facing 한국어 문구는 "데이터 방향성", "근거", "소스 간 일치도", "추가 확인 필요"를 중심으로 작성합니다.

## 서비스 경계

- `web`은 `main-server`를 호출합니다.
- `main-server`는 사용자-facing API 경계입니다.
- `agent-worker`는 수집, 정규화, 분석, 큐, LLM/RAG, 키움 가격 수집 데몬을 담당합니다.
- `packages/data-access`는 재사용 가능한 repository 계층을 담당합니다. SQL을 여러 서비스에 흩뿌리지 말고 repository를 우선 사용하세요.
- `packages/signal-core`는 공통 안전 규칙과 계약 헬퍼를 담당합니다.
- `database/migrations/`는 스키마 기준입니다.

수집/분석 로직을 `main-server`로 옮기지 마세요. 로컬 개발 도구가 명시적으로 요구하는 경우가 아니라면 `web`이 `agent-worker`를 직접 호출하게 만들지 마세요.

## DB 규칙

- 스키마의 유일한 기준은 `database/migrations/`입니다.
- 이미 적용된 migration은 절대 수정하지 말고 `python database/migrate.py new "..."`로 새 타임스탬프 migration을 추가하세요.
- 애플리케이션 코드나 임시 setup 스크립트에서 테이블을 만들지 마세요.
- DB 문서가 명시적으로 허용하지 않는 한 migration에서 `IF NOT EXISTS`를 사용하지 마세요.
- seed는 `ON CONFLICT` 기반으로 재실행 가능하게 만드세요.
- legacy `report_raw`, `report_signal`은 과거 Report MVP 경로를 위해서만 존재합니다. 신규 코드는 `raw_documents -> report_raw_details`와 정규화/분석 테이블을 사용해야 합니다.

## 현재 구현 상태

- DART 큐 핸들러는 `collect_dart`, `normalize_dart`, `analyze_dart` 및 ownership 경로 `collect_dart_ownership`, `normalize_dart_ownership`, Report는 `collect_report → process_report → normalize_report → analyze_report`가 구현되어 있습니다.
- (#11 업데이트) 워커의 **큐 드레인 데몬**(`app/orchestrator/queue/drain_daemon.py`, `QUEUE_DRAIN_DAEMON_ENABLED`)이 `processing_queue`를 체인 순서대로 끝단(`PUBLISH_SIGNALS` 발행)까지 연속 소비합니다(advisory-lock 단일 기동, 단발/CI 검증은 `run_worker_drain.py`). 스케줄러 인스턴스(`run_scheduler_instance.py`)가 수집·분석 작업을 주기 인큐합니다. 주가(PRICE) 예측은 `RiskReport.price_prediction`으로 **별도 제공**됩니다. DART는 현재 `direction="unknown"`, `data_status="no_signal"`인 근거·커버리지 소스로 집계에 합류하며 `score_breakdown.DART`와 `SYNTHESIZE`에는 남지만 숫자 `final_score` 평균에는 들어가지 않습니다. `backfill_dart_labels` 이벤트스터디 라벨 백필과 DART 소스 ML 채널은 운영 경로에서 제거되었습니다. 토폴로지는 [docs/architecture-diagram.md](docs/architecture-diagram.md) 참조.
- DataLab 수집은 카테고리 기반이며 `datalab_raw_documents -> datalab_raw_details -> processing_queue(stock_id=NULL)` 경로를 사용합니다.
- (#11 업데이트) 가격 수집은 기본적으로 **수집기 인스턴스**(`run_collector_instance.py`)에서 실행되며 `price_snapshots`, `ohlcv_data`에 저장합니다(`PRICE_COLLECTOR_ENABLED`로 워커 lifespan 내장 on/off).
- PRICE analyzer는 DB 데이터를 읽어야 하며 키움 API를 직접 호출하면 안 됩니다.
- Report 코드에는 아직 legacy 조각이 남아 있습니다. canonical schema 이전은 진행 중인 기술 부채로 취급하세요.

## LLM 및 분석 규칙

- 수치 값은 원천 데이터 또는 DB row에서 가져와야 하며 LLM이 만들어내면 안 됩니다.
- LLM 출력은 저장하거나 사용자에게 보여주기 전에 검증해야 합니다.
- LLM timeout, 잘못된 JSON, 금지 표현 감지 시 결정적 fallback을 제공해야 합니다.
- Collector는 LLM을 호출하지 않습니다. Collector는 원본 데이터를 수집/저장하고 후속 작업을 큐에 등록합니다.
- Analyzer는 정규화된 DB 데이터 또는 승인된 DB 데이터를 읽어야 하며 외부 수집 API를 직접 호출하지 않습니다.

## 개발 명령

저장소 루트 또는 각 서비스 디렉터리에서 `uv`를 사용합니다.

```powershell
uv sync --all-packages --group dev

cd services/main-server
uv run pytest

cd ../agent-worker
uv run pytest

cd ../../packages/data-access
uv run pytest

cd ../signal-core
uv run pytest
```

프론트엔드:

```powershell
cd web
npm test
```

데이터베이스:

```powershell
uv run python database/migrate.py status
uv run python database/migrate.py apply
uv run python database/tools/check_schema.py
```

## 작업 완료 전 확인

- 변경한 파일에 맞는 가장 좁고 유효한 테스트를 실행하세요.
- DB 스키마를 바꿨다면 가능한 경우 migration status/apply와 schema drift 검사를 실행하세요.
- 구현 동작이 바뀌면 관련 문서도 함께 업데이트하세요.
- 문서와 코드가 맞지 않는 부분을 발견하면 조용히 넘기지 말고 최종 응답에 명시하세요.
