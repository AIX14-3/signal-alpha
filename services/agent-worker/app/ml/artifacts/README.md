# app/ml/artifacts

메타러너(stacking) 등 ML 런타임이 로드하는 **오프라인 학습 산출물** 디렉터리.

## meta_learner.json (선택)

`app/ml/meta_learner.py` 가 로드하는 stacking 가중. **파일이 없으면 균등 가중 폴백**으로
동작하므로(기존 거동 보존), 학습 전에는 두지 않아도 된다.

```json
{
  "weights": {
    "ewma": 0.10,
    "har_rv": 0.45,
    "garch": 0.20,
    "lightgbm": 0.25
  }
}
```

- 값은 양수만 사용(0/음수는 무시). 런타임에 **현재 가용한 모델 부분집합으로 재정규화**되므로
  일부 모델이 빠져도(가용성 게이트) 안전하다.
- 경로는 `ML_META_LEARNER_ARTIFACT` 환경변수로 오버라이드 가능.
- 가중 학습은 worker가 아니라 별도 학습 파이프라인(`harness/`)에서 vol-benchmark
  out-of-fold 예측으로 산출하고, 그 결과 JSON만 여기에 떨군다.

> 실제 가중 파일은 학습 산출물이라 보통 레포에 커밋하지 않는다(또는 버전 태그와 함께 관리).
