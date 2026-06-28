# 워커 설계 설명서 + 팀 핸드오프 (#11 워커 영역 완성)

> 팀 공유용. 현재 워커 동작 설계 + "메타러너 예측 라인(주가 BASE 앵커 + 대체데이터 가산)"의 구현·
> 학습 상태(§5) + 다음 단계를 정리한다. 코드 수준 파이프라인: [worker-pipeline-detailed.md](./worker-pipeline-detailed.md),
> 토폴로지 그림: [architecture-diagram.md](../architecture-diagram.md). 동작 기준은 항상 코드/테스트. 최종 갱신: 2026-06-28.

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
COLLECT_* → NORMALIZE_* → ANALYZE_* → SRC_INFER → RETURN_COMBINE
  → AGGREGATE_SIGNAL → RISK_VETO → SYNTHESIZE → PUBLISH_SIGNALS
```

> 주가 변동성 ML 채널(`ML_INFER`/`META_COMBINE`)은 C안 Phase 1(#585)에서 **제거**됐다.
> 현재 ANALYZE 다음 ML 단계는 메타러너 예측 라인(`SRC_INFER`→`RETURN_COMBINE`, §5)뿐이다.

- env `QUEUE_DRAIN_DAEMON_ENABLED=true` 라야 동작(기본 off). advisory-lock 단일 기동(ops/price 데몬과 동일).
- 단발/CI 검증: `run_worker_drain.py`(`--watch` 연속). 이전엔 큐 자동소비 주체가 없어 리포트가 발행되지 않았다.

---

## 3. 소스별 라우팅 — "점수=주가, 대체데이터=근거, LLM이 합침"

핵심 설계 결정(#11). **점수를 뒤집지 않는다**:

- **주가(PRICE) ML/DL** → `RiskReport.price_prediction` 으로 **별도 정량 신호** 제공(방향+예측확률 score_100).
- **집계 점수(`final_score`)** = `SCORING_SOURCES`(현재 `{DART, HIRING, PATENT, DATALAB}`) 평균 — 대체데이터를
  소스별 독립 산입(C안 Phase 2, #584 — 단일 ALTERNATIVE collapse 폐기). 점수를 뒤집지 않는다.
- **DART·증권사리포트·대안데이터 = 근거** → 끝단 LLM 종합(`SYNTHESIZE`)이 *집계 점수 + 주가 예측 + 근거*를 합쳐 서술(temperature=0, 점수 불변).
  - **DART** → 끝단 LLM 정제(+ 치명 키워드는 `RISK_VETO` 결정론 룰). 메타러너 미사용.
  - **REPORT** → 투자의견(`signal_direction`) 컨센서스로 **결정론 방향**(`_report_consensus_direction`, 의견 없으면 no_signal 폴백).
- 메타러너 예측 라인(`SRC_INFER`)은 **구현·배선됨**(주가 BASE 앵커 + 대체데이터 가산 → 7예측률). §5 참조.

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

## 5. 메타러너 예측 라인 — 주가 BASE 앵커 + 대체데이터 가산 (구현됨)

> 핵심: **주가 예측률을 BASE(앵커)로 두고, 각 대체데이터(datalab·특허·채용·DART·리포트)를 주가에
> 융합해 소스별 예측률을 더한다.** 점수 헤드라인은 결정론 집계(§3) 유지, 7개 예측률은 **병행 노출**한다.
> 코드 수준 단계별 흐름·다이어그램: [worker-pipeline-detailed.md](./worker-pipeline-detailed.md).

**구현된 흐름**:
```
SRC_INFER (app/ml/source_inference.py)
  : 소스 정형 피처 → base 모델(src_price·src_datalab·src_hiring·src_dart·src_patent, LightGBM)
    → ml_inferences(run_key=SRC, forward-return). 아티팩트 부재 시 예측 None(graceful).
  → RETURN_COMBINE (app/ml/return_combine.py)
    : 각 소스 = combine_return({src_price, src_<source>}) 로 **주가 BASE 앵커 ⊕ 소스** 융합.
      소스별 6(SRC_PRICE/DATALAB/HIRING/DART/PATENT/REPORT) + 통합 1(SRC) = 7개를 meta_signals
      (per-source run_key)에 적재 + final_signals.source_predictions(JSONB) 오버레이.
  → SYNTHESIZE (app/synthesis/tasks.py)
    : RiskReport + LLM 컨텍스트에 7개 예측률 노출, LLM 은 서술만(점수 불변). 결정론 폴백도 한 줄 요약.
  → PUBLISH_SIGNALS → 백엔드 DB → api.signals_current.source_predictions → web.
```

**구현 상태(2026-06-28)**:
- P1 주가 BASE(`src_price`, 스케일-프리 피처) — PR #568 ✅ / P2 per-source 융합+특허 — #570 ✅
  / P3 7예측률 발행·노출(final_signals.source_predictions + api view) — #572 ✅ / P4 LLM 서술 — #574 ✅.
- `src_price` **학습 완료**(Neon 3년 OHLCV·20종목, OOF hit≈0.59, PoC). 학습 하니스: 주가=
  `app/ml/train_price_model.py`(OHLCV 밀집 패널), 이벤트형 소스=`app/ml/train_source_models.py`.
- **대체 4모델(datalab/hiring/dart/patent)은 미학습** — 원천 데이터 미적재(실적재 단계 필요).

**SRC_INFER 라이브 트리거 — 배선됨**:
- `ANALYZE_PRICE`(`orchestrator/price/tasks.py`)가 per-stock 1회 `SRC_INFER` 를 인큐한다 → 라인 라이브.
- 아티팩트 있는 소스만 예측(현재 `src_price`) → 7예측률 중 가능한 것부터 채워지고, 나머지는 None(graceful).
- E2E(로컬 PG + 학습된 src_price)로 `ANALYZE_PRICE → SRC_INFER → RETURN_COMBINE → final_signals.
  source_predictions → SYNTHESIZE` 노출까지 검증됨.

> 주의: 학습 채널은 라벨·데이터가 충분해야 의미가 있다(약신호는 단기 예측력 약함). 주가는 데이터가
> 풍부해 BASE 로 적합하고, 대체데이터는 적재 후 가산. 점수 산식(§3)은 어느 경우든 불변.

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
