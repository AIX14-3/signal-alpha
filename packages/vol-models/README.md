# signal-alpha-vol-models

`biop99999111/vol-benchmark`에서 **버전 고정 벤더링**한 변동성 예측 모델 + 시점안전(point-in-time)
`DataContract`. signal-alpha worker의 ML/DL 추론 단계(`app/ml/`)가 이 패키지를 import 해
동일 입력으로 여러 모델을 비교/결합한다.

## 구조

```
vol_models/
  common/
    data_contract.py   # ≤ asof 슬라이스만 노출 (no look-ahead)
    rv.py              # ret(log return) · rv_d(Garman-Klass 등 분산프록시) · H일 변동성 환산
    harness.py         # walk-forward 백테스트 러너 (CLI 단독 실행용; 워커는 predict()만 사용)
  models/
    cpu_ewma.py        # EWMA(RiskMetrics) 기준선 — 순수 numpy
    # (PR2에서 HAR-RV/GARCH/LightGBM/Kronos/Chronos-2 추가)
```

## 모델 인터페이스 (벤더 원본과 동일)

```python
def predict(contract: DataContract, asof_idx: int, horizon: int, cfg: dict, rng) -> float
```
모든 모델은 `asof_idx` 시점까지의 데이터만 보고 다음 `horizon`일 실현변동성을 예측한다.

## 벤더링 원칙

- 원본 로직은 **변경하지 않는다**(비교 가능성 보존). 패키지화를 위한 import 경로만
  `vol_models.common.*` 로 조정한다.
- 무거운 백엔드(arch/lightgbm/torch)는 optional-extras로 분리 — base 설치는 numpy/pandas만.
- 업스트림 갱신 시 이 디렉터리를 동기화하고 버전을 올린다.
