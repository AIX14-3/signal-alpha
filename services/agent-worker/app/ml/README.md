# 모델 경연 하니스 (`app/ml`)

여러 ML 모델을 **같은 데이터 · 같은 평가**로 한 번에 학습·비교해서, 어떤 모델이
(있다면) 미래 주가 움직임을 가장 잘 맞히는지 보는 도구입니다.

## 빠른 실행 (합성 데이터)

```bash
# 1) ML 라이브러리 설치 (런타임 컨테이너엔 안 들어감)
uv sync --extra ml          # 또는: pip install scikit-learn numpy scipy

# 2) 모델 경연 실행 — 가짜(합성) 데이터로 비교표 출력
python -m app.ml.bakeoff

# 옵션
python -m app.ml.bakeoff --csv out.csv     # 전체 지표 CSV로 저장
python -m app.ml.bakeoff --noise 2.0       # 문제를 더 어렵게 (신호 약화)
python -m app.ml.bakeoff --folds 6 --seed 1
```

## DataLab 단독 테스트 (실제 파이프라인)

합성 피처가 아니라 **실제 DataLab 변환 코드**(`compute_indicators` → 피처 → 라벨)를
통과시키는 두 가지 모드:

```bash
# (a) 데모 데이터 — DB 없이 배관 전체 검증 (실제 변환 코드 사용)
python -m app.ml.bakeoff --source datalab-demo --weeks 104

# (b) 실제 데이터 — DataLab은 DB, 주가는 로컬 CSV (ohlcv_data 안 건드림)
DATABASE_URL=... python -m app.ml.bakeoff --source datalab-db \
    --tickers 005930,000660,035420 --start 2021-01-01 --end 2023-12-31 \
    --prices-csv prices_2021_2023.csv --benchmark KS11

# 라벨/피처 튜닝 노브 (양쪽 공통)
#   --lookback 30   피처 룩백(일)   --horizon 5   라벨 기간(거래일)
#   --band 0.3      중립밴드(%)
```

`datalab-db`는 표본이 0이면 "데이터가 적재됐는지" 안내하며 종료합니다(`dropped` 사유 포함).

### 데이터 적재 워크플로 (2021~2023)

```bash
# 1) 주가+KOSPI → 로컬 CSV (키 불필요)
pip install finance-datareader
python scripts/backfill_prices_fdr.py --tickers 005930,000660,035420 \
    --benchmark KS11 --start 2021-01-01 --end 2023-12-31 --out prices_2021_2023.csv

# 2) DataLab → DB (NAVER_CLIENT_ID/SECRET + DATABASE_URL 필요, 카테고리/매핑 선시드)
python scripts/backfill_datalab.py --start-year 2021 --end-year 2023

# 3) 위 (b) 명령으로 모델 경연 실행
```

- 주가는 **키움(Kiwoom) 키 불필요** — FinanceDataReader가 과거 일봉을 무료 제공.
- `ohlcv_data`(주가팀 테이블)는 건드리지 않음 — 주가는 CSV로만 흐름.

`xgboost`/`lightgbm`/`catboost`가 설치돼 있으면(`uv sync --extra ml-boost`) 경연에
자동으로 합류하고, 없으면 조용히 빠집니다.

## 비교표 읽는 법

| 묶음 | 컬럼 | 뜻 |
|---|---|---|
| A. 방향 | `acc`, `f1`, `roc_auc`, **`Dbase`** | 방향을 맞히나 / **다수 찍기(baseline)보다 나은가** |
| B. 크기 | **`IC`**, `rankIC` | 모델 점수가 실제 수익률과 같이 움직이나(상관) |
| C. 경제성 | `dec_sprd` | 가장 강세로 본 10% − 가장 약세로 본 10%의 실제 수익률 차 |
| D. 견고성 | `sd_acc`, `sd_IC` | fold(시기)마다 들쭉날쭉한가 (작을수록 신뢰) |

**판정:** `Dbase>0` + `IC>0` + `rankIC>0` + `sd_*`가 작아야 "진짜 실력".
baseline은 표 맨 아래 고정 — 모델이 이걸 못 이기면 학습한 게 없는 것.

## ⚠️ 합성 데이터 ≠ 진짜 결과

지금 표는 **"코드가 도는지 + 표가 어떻게 생겼는지"** 만 보여줍니다(가짜 연료).
"우리 대체데이터가 실제로 주가를 맞히나"는 **진짜 라벨이 쌓여야** 알 수 있습니다:

```
final_signals 축적  →  채점 배치(신호 후 N일 주가로 is_hit 계산)  →
backtest_results 라벨  →  features.py(피처) + labels.py(라벨)로 X,y 구성  →  하니스
```

`run_bakeoff(X, y, excess_returns, dates)` 는 데이터 출처를 가리지 않으므로, 합성
생성기(`synthetic.py`)만 실제 로더로 바꾸면 그대로 진짜 평가가 됩니다.

## 구성

| 파일 | 역할 | 의존성 |
|---|---|---|
| `labels.py` | 정답 만들기 (초과수익률·중립밴드·is_hit) | 순수 파이썬 |
| `features.py` | `indicators.py` 출력 → 피처 행렬 | 순수 파이썬 |
| `models.py` | 모델 레지스트리 (sklearn + 선택 부스터) | scikit-learn |
| `evaluation.py` | 워크포워드 분할 + 4묶음 지표 | numpy/scipy/sklearn |
| `report.py` | 비교표 / CSV 렌더링 | 표준 라이브러리 |
| `synthetic.py` | 합성 피처 행렬 (스모크 테스트용) | numpy |
| `datalab_dataset.py` | DataLab 행+주가 → (X, y) **빌더** (누수 차단) | numpy |
| `datalab_demo.py` | 현실적 DataLab+주가 데모 행 생성 | numpy |
| `datalab_db.py` | 실제 DB에서 DataLab+주가 적재 (저장소 재사용) | data-access |
| `bakeoff.py` | 실행 진입점 (`python -m app.ml.bakeoff`) | 위 전부 |

테스트:
- `pytest tests/test_ml_harness.py` — 라벨 로직 + 워크포워드 누수 가드
- `pytest tests/test_ml_datalab_dataset.py` — DataLab 데이터셋 빌더(라벨 기간·룩백 누수·드롭)
