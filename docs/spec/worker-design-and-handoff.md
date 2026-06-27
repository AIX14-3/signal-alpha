# 워커 설계 설명서 + 팀 핸드오프 (#11 워커 영역 완성)

> 팀 공유용. 현재 워커 동작 설계 + "메타러너+대체데이터→LLM 예측 라인"을 **연결 OFF 상태로
> 미리 만들어 둔 plug-in 지점** + 다음 단계를 정리한다. 토폴로지 그림: [architecture-diagram.md](../architecture-diagram.md).
> 동작 기준은 항상 코드/테스트. 최종 갱신: 2026-06-28.

---

## 1. 배포 토폴로지 (5 컴퓨트 유닛 + DB 2 인스턴스)

`services/agent-worker` **한 코드베이스**가 세 유닛으로 기동된다:

| 유닛 | 엔트리포인트 | 역할 |
|---|---|---|
| **worker** | uvicorn + **큐 드레인 데몬** | API 8라우트 + `processing_queue` 를 끝단(발행)까지 연속 소비 |
| **collector** | `run_collector_instance.py` | 키움 실시간 가격 데몬 + `run_collectors.py`(patent/datalab) |
| **scheduler** | `run_scheduler_instance.py` | 워커 `/internal/schedules/*` 주기 HTTP 호출(수집 스케줄) |

+ DB 2 인스턴스(수집 DB / 백엔드 DB, #531). 단일 통합 인스턴스 기동도 가능
(`PRICE_COLLECTOR_ENABLED=true` + `QUEUE_DRAIN_DAEMON_ENABLED=true` 동시 on — 데모/로컬).

> 팀 스케줄러 경계(`docs/runbooks/agent-pipeline-schedule.md`): 스케줄러는 로직 없이 워커 엔드포인트만 호출.

---

## 2. 큐 드레인 데몬 (발행의 핵심)

`app/orchestrator/queue/drain_daemon.py` — `processing_queue` 를 체인 순서로 끝단까지 연속 소비한다:

```
COLLECT_* → NORMALIZE_* → ANALYZE_* → ML_INFER → META_COMBINE
  → AGGREGATE_SIGNAL → RISK_VETO → SYNTHESIZE → PUBLISH_SIGNALS
```

- env `QUEUE_DRAIN_DAEMON_ENABLED=true` 라야 동작(기본 off). advisory-lock 단일 기동(ops/price 데몬과 동일).
- 단발/CI 검증: `run_worker_drain.py`(`--watch` 연속). 이전엔 큐 자동소비 주체가 없어 리포트가 발행되지 않았다.

---

## 3. 소스별 라우팅 — "점수=주가, 대체데이터=근거, LLM이 합침"

핵심 설계 결정(#11). **점수를 뒤집지 않는다**:

- **주가(PRICE) ML/DL** → `RiskReport.price_prediction` 으로 **별도 정량 신호** 제공(방향+예측확률 score_100).
- **집계 점수(`final_score`)** = `SCORING_SOURCES`(현재 `{DART, ALTERNATIVE}`) 평균 — *유지*(대체데이터 기여 보존).
- **DART·증권사리포트·대안데이터 = 근거** → 끝단 LLM 종합(`SYNTHESIZE`)이 *집계 점수 + 주가 예측 + 근거*를 합쳐 서술(temperature=0, 점수 불변).
  - **DART** → 끝단 LLM 정제(+ 치명 키워드는 `RISK_VETO` 결정론 룰). 메타러너 미사용.
  - **REPORT** → 투자의견(`signal_direction`) 컨센서스로 **결정론 방향**(`_report_consensus_direction`, 의견 없으면 no_signal 폴백).
- 메타러너 학습 채널(`SRC_INFER`)은 **코드만 있고 라이브 미배선**(§5 참조).

**소스 출력 계약**(각 분석기가 내는 `agent_results.method_detail`):
`{ source, source_score(-1~1), direction(positive/negative/neutral/mixed/unknown), data_status(ok/partial/failed/no_signal), summary, risk_flags }`.
검증기: `app/orchestrator/aggregation/source_contract.py` (`validate_source_method_detail` 로 DB 없이 self-check).

> **PRICE 적재 버그픽스(중요)**: PRICE 핸들러 `analysis_mode` 가 `price_only`(DB CHECK 미허용)라 적재 실패 →
> `full` 로 수정됨. 새 소스 추가 시 `analysis_mode ∈ {full, dart_only, quick}` 만 사용(가드: `tests/test_analysis_mode_contract.py`).

---

## 4. 팀 분업 — 소스 = 독립 모듈

각 팀원은 자기 소스 1개를 **독립 모듈**로 소유: 수집기(`app/collectors/*`) + 분석기(`app/analyzers/*`) +
소스 결과(`method_detail` 계약 + 소스별 `final_signals`). 공통 베이스라인 공유 안 함 → 충돌 없이 병렬 개발.
산출물이 §3 계약을 만족하면 집계/LLM이 자동 합류한다.

---

## 5. 🔌 메타러너 + 대체데이터 → LLM 예측 라인 (연결 OFF, plug-in 지점)

> 목적: 팀원이 자기 대체데이터로 **학습형 예측치**를 만들어 합류시킬 수 있게 **골격을 미리 깔아두고
> 연결선만 OFF**. 문서대로 plug-in 하면 켜진다. 데이터/라벨이 쌓이기 전엔 OFF가 정상.

**설계된 흐름(코드 존재, 미배선)**:
```
ANALYZE_ALTERNATIVE/REPORT (소스 정형 피처 적재)
  → [OFF] SRC_INFER  (app/ml/source_inference.py)
        : 소스 피처 → base 모델(src_datalab/src_hiring, LightGBM) → ml_inferences(run_key=SRC, forward-return)
  → RETURN_COMBINE (app/ml/return_combine.py)
        : src_* + Report 피처 결합 → meta_signals(run_key=SRC) return 컬럼(final_score/direction/confidence)
  → [미배선] AGGREGATE/SYNTHESIZE 가 meta_signals(SRC) 를 읽어 합류
```

**OFF 스위치(현재 미배선 지점)** — 켜려면 여기를 연결:
1. **SRC_INFER 인큐**: 라이브 경로에서 아무도 `SRC_INFER` 를 인큐하지 않는다. `ANALYZE_ALTERNATIVE`/`ANALYZE_REPORT`
   핸들러 끝에서 `enqueue(task_type=SRC_INFER, ...)` 추가(env gate 권장: `SRC_INFER_ENABLED`).
2. **base 모델 아티팩트**: `app/ml/artifacts/source_models/{src_datalab,src_hiring,...}.txt`(LightGBM Booster).
   없으면 예측 None(graceful skip). 학습: `app/ml/train_source_models.py`.
3. **합류부**: `AGGREGATE`(`aggregation/tasks.py`) 또는 `SYNTHESIZE`(`synthesis/tasks.py`) 가
   `meta_signals(run_key=SRC)` 를 읽어 `price_prediction` 옆에 **별도 예측치**로 노출(점수 산식은 §3 유지 권장).

**팀원 plug-in 절차(자기 소스 학습 합류)**:
- (a) 소스 정형 피처를 `app/ml/source_features.py` 계약대로 어셈블(이미 datalab/hiring/dart 로더 있음).
- (b) `train_source_models.py` 로 base 모델 아티팩트 생성(라벨 = forward-return, L6 abnormal_return_20d 정렬).
- (c) 위 OFF 스위치 1~3 을 env gate 로 켠다. 끄면 기존 동작(점수=주가/집계, 대체=근거) 그대로.

> 주의: 학습 채널은 라벨·데이터가 충분해야 의미가 있다(약신호는 단기 예측력 약함). 우선은 §3 결정론/LLM 라인으로
> 동작시키고, 데이터 쌓인 뒤 REPORT(컨센서스 상관 높음)부터 학습 채널을 켜는 것을 권장.

---

## 6. 다음 단계 (자동화·외부연결·실적재·E2E)

1. **자동 구동 + 외부관리**: worker(드레인 on)·collector·scheduler 를 상시 프로세스/컨테이너로. 외부 스케줄러
   (Cloud Scheduler/cron/GitHub Actions/팀 `ops/*.ps1`)가 워커 `/internal/schedules/*` 호출. 관측은
   `observability`/`dead_letter` 라우트 + ops 데몬. 외부 연결성(헬스/큐 상태 API, 알림 webhook) 추가.
2. **실적재(real ingestion)**: DART/Kiwoom/Naver 키 설정 + DART corp_code sync 선행([[signal-alpha-report-autopublish]]) →
   scheduler 로 수집 스케줄 인큐 → 드레인 데몬이 발행까지 소비.
3. **E2E 점검**: 종목 enqueue → 드레인 → `final_signals` + RiskReport(`price_prediction` 포함) 발행 → 백엔드 DB
   publish → web 표시. 로컬 검증 방법은 메모리 RESUME 참조(로컬 도커 PG + 합성 OHLCV + ALTERNATIVE 스코어링 시드).

---

## 7. 빠른 참조 (env/엔트리포인트)

```
# 워커(단일 통합 데모)
QUEUE_DRAIN_DAEMON_ENABLED=true  PRICE_COLLECTOR_ENABLED=true   → uvicorn
# 분리 배포
worker:    QUEUE_DRAIN_DAEMON_ENABLED=true, PRICE_COLLECTOR_ENABLED=false
collector: uv run python run_collector_instance.py
scheduler: uv run python run_scheduler_instance.py   # 워커 /internal/schedules/* 주기 호출
# 검증
uv run python run_worker_drain.py        # 큐 단발 드레인(발행까지)
uv run python -m pytest tests/           # 1030+ 통과
```
