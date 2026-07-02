# 특허 ML 재검증 — 테스트 보고서 (2026-06-29)

> 특허 ML "무신호 재검증"(Stage 1~4)에서 하니스(`app/ml/research/`)에 가한 코드 변경의 **검증 상태** 정리. 단위 테스트 + 회귀(전후 동치) + 정적분석(ruff) 기준.
> 
> **한 줄 결론: ML 하니스 단위 테스트 39개 전부 GREEN, ruff clean. 핵심 신규 로직(횡단면 정규화·날짜별 IC·융합 join)에 단위 테스트 신설. 회귀=정규화 OFF가 어제 수치 정확 재현. 미커버=event_study 스크립트·load_fusion async DB 경로(통합테스트 영역).**

---

## 1. 테스트 환경

| 항목 | 값 |
| --- | --- |
| 러너 | pytest 9.0.3 · Python 3.11.15 · win32 |
| 대상 | `services/agent-worker/tests/test_ml_*.py` |
| 명령 | `uv run python -m pytest tests/test_ml_*.py -v` |
| 정적분석 | `ruff check app/ml/research/* scripts/patent_event_study.py scripts/enrich_patents_llm.py` |

## 2. 결과 요약

- **단위 테스트: 39 passed / 0 failed** (12.7s). 신규 8개 추가(patent_xs 4 + fusion 4).
- **ruff: All checks passed** (변경/신규 파일 전부). 정리한 기존 미사용 import 2건(weekly_signal_dates, typing.Any).
- **회귀**: `bakeoff --source patent-db --xs-normalize none --feature-set all`이 어제 무신호 수치를 **정확 재현**(hist_grad_boost rankIC +0.016, stacking −0.051) → 새 코드가 기존 경로를 바꾸지 않음 입증.
## 3. 테스트 파일별 커버리지 (39개)

| 파일 | 개수 | 무엇을 검증 |
| --- | --- | --- |
| `test_ml_harness.py` | 10 | 라벨(neutral band·초과수익·방향)·feature_row 평탄화·feature_matrix 정렬·워크포워드(미래 미학습·확장윈도·최소날짜) |
| `test_ml_datalab_dataset.py` | 8 | forward return 미래종가만·라벨/드롭카운트·벤치마크 차감·미래행 무시(누수)·최소관측·주간샘플·CSV 로더 |
| `test_ml_patent_xs.py` ⭐신규 | 4 | **횡단면 정규화**(rank=크기레벨제거·순위보존, zscore=날짜중심, none=passthrough+NaN보존), **날짜별 IC**(per-date Spearman 평균) |
| `test_ml_fusion.py` ⭐신규 | 4 | **융합 join**((stock,date)→행 인덱스 유일성), **전체 rank 정규화**(모든 열 날짜내 percentile·NaN보존·얇은날짜 skip·빈행렬 안전) |
| `test_ml_hiring_dataset.py` | 8 | 계절성·미래공고 무시·YoY·라벨방향·벤치마크·최소관측·가격없음 드롭 |
| `test_ml_hiring_db.py` | 5 | source_name 정밀매칭(접미사/대소문자·부분문자열 오귀속 방지·모호명 드롭) |

## 4. 이번 세션 신규 로직과 테스트 매핑

| 신규/변경 로직 | 파일:함수 | 테스트 |
| --- | --- | --- |
| 횡단면 rank/z 정규화 | `patent_dataset._cross_sectional_normalize` | `test_ml_patent_xs` 3건 ✅ |
| 날짜별 횡단면 IC | `evaluation._xs_rank_ic` | `test_ml_patent_xs::test_xs_rank_ic_averages_per_date` ✅ |
| 死피처 제외(feature_set) | `patent_dataset.build_dataset(exclude_features)` | 회귀 비교(피처 11→7)로 간접 검증 |
| 융합 (stock,date) join | `fusion_db._index_dataset` | `test_ml_fusion::test_index_dataset_maps_stock_date_to_row` ✅ |
| 융합 전체 rank 정규화 | `fusion_db._rank_all_cross_sectional` | `test_ml_fusion` 3건 ✅ |
| permutation null(rank_ic_xs) | `bakeoff.run_permutation` | 실데이터 검정으로 검증(트리 p<0.001) — 단위테스트 없음 |
| enrich 500건 커밋 재개성 | `enrich_patents_llm` | 통합영역(외부 API)·미단위테스트 |

## 5. 검증 방법론 (단위테스트 외)

- **회귀 동치**: 정규화 OFF 경로가 변경 전 수치를 재현 → 기존 사용자 영향 없음.
- **실데이터 sanity**: 각 bakeoff 출력의 samples/dropped/up-rate가 합리적 범위인지 확인(예: 레버1 samples=4093, dropped 사유 추적).
- **누수(look-ahead) 가드**: 윈도잉은 publication_date 기준(테스트 `test_features_ignore_future_*`로 미래행 무시 검증), 워크포워드 날짜경계 분할(테스트 `test_walk_forward_never_trains_on_the_future`).
- **통계 엄밀성**: permutation null + 비겹침(signal-step≥horizon) 교차검증을 *실험 게이트*로 적용(코드가 아닌 실행 프로토콜).
## 6. 미커버 / 한계 (정직)

- **`scripts/patent_event_study.py`**: 단위 테스트 없음. CAR/vol 계산은 실데이터로만 sanity 확인(8041 이벤트). 순수함수(`_returns`·`_car_for_event`)는 향후 단위테스트 가능.
- **`fusion_db.load_fusion`(async DB 경로)**: 통합 테스트 영역(3 로더+DB 필요). 순수 join/정규화 헬퍼만 단위 커버.
- **`run_permutation`**: 단위테스트 없음(무작위·느림). 실데이터 검정으로 대체 검증.
- **enrich**: 외부 BQ/Gemini 의존 → 통합영역, 미단위테스트.
- 커버리지 수치(line %) 미측정 — pytest-cov 미구성.
## 7. 재현

```plain text
cd services/agent-worker
uv run python -m pytest tests/test_ml_harness.py tests/test_ml_datalab_dataset.py \
  tests/test_ml_patent_xs.py tests/test_ml_fusion.py tests/test_ml_hiring_dataset.py \
  tests/test_ml_hiring_db.py -v
uv run ruff check app/ml/research/ scripts/patent_event_study.py
```

## 8. 권고

- [ ] event_study 순수함수(`_car_for_event`·`_returns`)에 단위테스트 추가(공개일 매핑·윈도우 경계).
- [ ] pytest-cov 도입해 `app/ml/research/` 라인 커버리지 가시화.
- [ ] (선택) `load_fusion`에 작은 인메모리 fixture 통합테스트(3소스 mock Dataset join).

---

관련: [[patent-ml-rejected]] · 리포트 `2026-06-26-patent-ml-rigorous-reverify.md`·`2026-06-26-patent-stage234-eventstudy-fusion.md`
