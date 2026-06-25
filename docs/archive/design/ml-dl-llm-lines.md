# ML / DL / LLM 라인 — 모델 확정 + I/O 구체화 (MVP)

[`architecture.mermaid`](./architecture.mermaid) · [`worker-redesign.md`](./worker-redesign.md) 의 게이트형
파이프라인에서 **판정에 쓰는 3개 "라인"** 을 모델·입출력·파라미터·게이트 단위로 구체화한다. 본 문서는
MVP에서 **확정된 모델**과, 합성 OHLCV로 끝까지 돌려 얻은 **첫 결과값(베이스라인)** 의 기준점이다.

핵심 원칙(불변): **판정(점수·방향·발행·결합 변동성)은 결정론 규칙·ML·게이트가 정하고, LLM은 설명만 한다.**

---

## 한눈에 — 라인별 확정

| 라인 | 단계(task_type) | 확정 모델 | 입력 → 출력 | MVP 실행 |
|---|---|---|---|---|
| **ML** | `ml_infer` → `meta_combine` | EWMA · HAR-RV · GARCH(1,1) · LightGBM | `ohlcv_data` → `ml_inferences.pred_value` → `meta_signals.combined_vol` | ✅ CPU |
| **DL** | `ml_infer`(가용성 게이트) | Kronos · Chronos-2 | 동일 `DataContract` → `ml_inferences.pred_value` | ⛔ 설계만(GPU 미실행) |
| **LLM** | `synthesize` | Gemini 3.x (`gemini-3.1-pro-preview`) | `final_signals`+`meta_signals`+근거 → 내러티브 | ✅ 키 주입 시 |

---

## ML 라인 (확정, MVP 실행)

벤더링된 vol-benchmark 모델(`packages/vol-models`)로 **h일 변동성**을 예측한다. 4종 모두 실구현이며
`run_inference`(`app/ml/inference.py`)가 모델별 독립 실패를 격리해 `ml_inferences` 에 멱등 적재한다.

| 모델 | 구현 | 백엔드 | 성격 |
|---|---|---|---|
| `ewma` | `vol_models/models/cpu_ewma.py` | numpy(순수) | RiskMetrics λ=0.94 기준선(항상 가용) |
| `har_rv` | `cpu_harrv.py` | numpy(순수) | Corsi(2009) 일/주(5)/월(22) 회귀 |
| `garch` | `cpu_garch.py` | `arch>=6.0` | GARCH(1,1) 조건부 분산 |
| `lightgbm` | `cpu_lgbm.py` | `lightgbm>=4.0`(+`scikit-learn`) | 트리, 가격 lag 피처 (alt-data 융합은 TODO) |

- **입력**: `ohlcv_data` 최신 `ML_LOOKBACK_SESSIONS`(400) 세션 → `contract_adapter.build_contract` →
  point-in-time-safe `DataContract`(date,ticker,OHLCV,ret,rv_d). look-ahead 없음.
- **출력**: 모델×asof×horizon 당 `ml_inferences.pred_value`(실패 시 NULL+`error_message`).
- **파라미터(env)**: `ML_HORIZON=10` · `ML_LOOKBACK_SESSIONS=400` · `ML_SEED=42` · `ML_RUN_KEY=ML`.
- **게이트**: ① 백테스트 게이트 `ML_GATE_PASSED_MODELS`(기본 4종) ② 가용성 게이트(`HAVE_*` import 플래그).
  `model_registry.resolve_models()` 가 교집합만 추론.

### 결합 — 메타러너 (`meta_combine` → `meta_signals`)
- `app/ml/meta_learner.combine` 이 게이트 통과 `pred_value` 들을 **stacking** 으로 1개 `combined_vol`
  + `confidence`(모델 합의도) 로 합쳐 `meta_signals` 에 적재.
- 학습 가중치 `app/ml/artifacts/meta_learner.json` 가 **없으면 균등 폴백**(`method=equal_fallback`).
  MVP는 균등 폴백으로 동작 → 후속 harness 에서 OOF 학습으로 `stacking` 전환.

---

## DL 라인 (설계 확정, MVP 미실행)

GPU 생성형/파운데이션 모델. ML 라인과 **동일한 `DataContract` 인터페이스**(`predict(contract, asof_idx,
horizon, cfg, rng) -> float`)라 추가 와이어링 없이 화이트리스트 편입만으로 합류한다.

| 모델 | 구현 | 백엔드 | 비고 |
|---|---|---|---|
| `kronos` | `vol_models/models/gpu_kronos.py` | `torch>=2.2` + Kronos | OHLCV→경로 샘플(n_samples=100) |
| `chronos2` | `gpu_chronos2.py` | `torch>=2.2` + chronos-forecasting | Amazon Chronos-2 |

- **MVP 처리**: CPU 호스트에서 가용성 게이트가 자동 제외 → `ml_inferences` 미생성(정상). 설계도엔
  점선/회색으로 "설계만" 표기.
- **편입 절차(후속)**: vast.ai GPU에서 실행 검증 → vol-benchmark `comparison.csv`(DM<0 & p<0.05)
  통과 시 `ML_GATE_PASSED_MODELS` 에 추가.

---

## LLM 라인 (확정)

끝단 **설명 전용**. 수치·판정은 절대 바꾸지 않는다.

- **단계**: `synthesize`(`app/synthesis/`). `final_signals`(signal/score/warning_level/is_published)
  + 최신 `meta_signals`(`ml_risk` 로 combined_vol/confidence) + 근거 `signal_events` → 내러티브.
- **출력**: `{headline, narrative, key_points, caution_points}` JSON. `final_signals.summary/bull/bear` 는
  LLM이 더 풍부한 내러티브를 만들 때만 갱신(`source="llm"`), 아니면 결정론 폴백 보존.
- **모델(확정)**: Gemini 3.x — `SYNTHESIS_LLM_MODEL=gemini-3.1-pro-preview`(현행 3.x Pro).
  클라이언트 `GeminiGenerateContentClient`(v1beta `:generateContent`, `response_mime_type=application/json`).
- **안전장치**: `_reject_investment_advice` 가 매수/매도/목표가 등 **지시형 투자조언**을 차단(서술형은 통과).
  LLM 미설정·타임아웃·검증 실패 → **결정론 폴백 내러티브**로 안전 강등(흐름 불변).
- **토글**: `SYNTHESIS_USE_LLM=on` + `GEMINI_API_KEY` 주입 시 활성. 미설정 시 off(폴백).

---

## MVP 베이스라인 (합성 OHLCV, 2026-06-25, h=10)

`seed_synthetic_ohlcv.py` 로 6종목×420세션 합성 적재 후 `run_pipeline.py` 로 산출. **모델 파이프라인이
끝까지 도는지 + 라인 간 결선**(ML `combined_vol` → LLM `ml_risk`) 검증이 목적이며 수치 자체는 합성값이다.

| ticker | ewma | har_rv | garch | lightgbm | combined_vol | conf | method |
|---|---|---|---|---|---|---|---|
| 005930 | 0.0674 | 0.1178 | 0.0626 | 0.0499 | 0.0744 | 0.65 | equal_fallback |
| 000660 | 0.0594 | 0.1178 | 0.0658 | 0.0766 | 0.0799 | 0.72 | equal_fallback |
| 035720 | 0.0627 | 0.1047 | 0.0602 | 0.0645 | 0.0730 | 0.75 | equal_fallback |

(전체 6종목 → `data/mvp_baseline_<asof>.csv`. 단일 RiskReport → `data/report_<ticker>.json`.)

재현:
```bash
uv run python services/agent-worker/seed_synthetic_ohlcv.py --limit 6 --sessions 420
uv run python services/agent-worker/run_pipeline.py --ticker 005930 --out data/report_005930.json --reset
uv run python services/agent-worker/run_pipeline.py --tickers 005930,000660,042700,035420,035720,259960 --reset
```

---

## 추천 라인 (결정론 — 끝단 뒤)

ML/DL/LLM 라인 산출을 **결정론 "주목·관심 추천"** 으로 랭킹하는 단계. **투자 조언이 아니다**
(매수/매도 지시 없이 발행 신호의 확신·안정성으로 줄을 세운다 — LLM/조언 가드레일 준수).

- **입력**: 발행 `final_signals`(5소스 종합 리포트: signal·final_score·confidence) + `meta_signals`(ML combined_vol).
- **점수(결정론)**: `recommendation_score = 100 · dir_w · conf_w · vol_w`
  - `dir_w` = `final_score/100`(발행 신호) | `0.5`(중립; 신호 없는 종목은 meta로 보완)
  - `conf_w` = 신뢰도 (final: `confidence/100`, meta: 모델 합의도 0..1)
  - `vol_w` = `1/(1 + REC_VOL_PENALTY · combined_vol)` — **변동성 역가중**(안정적일수록 ↑), `REC_VOL_PENALTY` 기본 5
  - 랭킹: 점수 내림차순, 동점은 stock_id. `basis`=`final`|`meta`.
- **출력**: `recommendations` 테이블(마이그레이션 `20260625_1227_recommendations.sql`, 자연키
  `(stock_id, asof_date, run_key)` 멱등) + `RecommendationRepository`(data-access) + CSV.
- **러너**: `services/agent-worker/run_recommend.py` — `SignalRepository.list_current_by_stock_ids`(발행 finals)
  + `MetaSignalRepository.latest_for_stock`(meta) 재사용, raw SQL 없이 repository 계층으로 적재.

베이스라인 산출(2026-06-25, vol_penalty=5): 005930(발행 positive)=41.98로 1위, 나머지 5종목은 meta 기준
(중립 방향) 신뢰도·변동성순. → `data/recommendations_<asof>.csv`.

재현: `uv run python services/agent-worker/run_recommend.py`

---

## 다음 단계 (베이스라인 → 고도화)

1. **메타러너 학습 harness**: vol-benchmark OOF 예측으로 `meta_learner.json` 산출 → `equal_fallback` 탈출.
2. **LightGBM alt-data 융합**: `cpu_lgbm.py` TODO(naver_kw_z, dart_event_decay) 활성화.
3. **DL 라인 GPU 검증**: Kronos/Chronos-2 vast.ai 실행 → 백테스트 게이트 통과 시 화이트리스트 편입.
4. **실데이터 백필**: Kiwoom 모의키(실시세)+DART로 합성 시드 대체.
5. **LLM 라인 실측**: `GEMINI_API_KEY` 주입 후 `gemini-3.1-pro-preview` 내러티브 품질·투자조언 차단 실검증.
