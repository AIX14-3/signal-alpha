# DataLab 시대별 키워드 ML 실험 (Stage 5) — 결정적 대조

**날짜**: 2026-06-25
**worktree/브랜치**: `sa-ml-longhorizon` / `feat/ml-datalab-longhorizon`
**관련**: 2026-06-23 DataLab ML bake-off(단독 무신호), 2026-06-24 long-horizon 피벗(특허→키워드 파이프라인)

## 가설

6/23 실험에서 **고정 키워드 DataLab 검색량은 주가 예측력 없음**(rankIC≈0)이 확정됐다.
남은 질문: 그 무신호가 *"고정 키워드를 써서"* 인가, 아니면 *"그 시기에 실제 뜨던 키워드(특허 제목에서
추출)를 측정하면 살아나는가"* 인가? 이를 가르기 위해 같은 하니스에서 두 모드를 비교한다.

- **period_keyword (처치군)**: 키워드는 `first_avail_date`(특허 공개일) 이후에만 피처에 들어감
  (point-in-time 게이트). "그 시점에 알 수 있던 트렌드 키워드만 측정."
- **fixed_keyword (대조군)**: 같은 키워드 풀·같은 검색 시리즈·같은 가격·같은 라벨, **게이트만 제거**
  (모든 키워드가 전 기간 활성). PIT 타이밍의 가치만 격리.

## 셋업

- **종목 3개**: 삼성전자(005930)·SK하이닉스(000660)·네이버(035420).
- **키워드**: 특허 제목(BigQuery 공개일) → 시대별 급상승 용어 추출(Stage2, LLM 아님=빈도 surge) →
  각 키워드 DataLab **전체기간 1회** 주간 수집(Stage3+4, 연도별 정규화 함정 해결).
  검색 데이터 있는 생존 키워드 = 네이버 47 + 삼성 33 + 하이닉스 37 = **117개**.
- **피처**(집합 불변 집계, prefix `period_keyword__`): `n_active`, `mean_level`, `mean_momentum`,
  `max_momentum`, `breadth`(상승 비율), `spike_count`.
- **라벨/리키지/평가**: 기존 하니스 그대로 재사용 — 미래 초과수익률(벤치=KS11), 중립밴드 드롭,
  walk-forward(train_date < test_date), horizon별 비겹침 signal-step.
- **기간**: 2016-01-01 ~ 2026-06-22. horizon 그리드 [1d, 1w, 1mo, 3mo, 6mo, 1y, 2y].

## 결과

`best_model`(16모델 중 최댓값)은 **선택 편향**으로 부풀려지므로, 정직한 지표는 **median 모델 rankIC**.

| horizon | 표본 | period best_IC | period **median_IC** | fixed best_IC | fixed **median_IC** |
|---|---|---|---|---|---|
| 1d | 1259 | +0.043 | **−0.018** | +0.014 | **−0.009** |
| 1w | 1435 | +0.066 | **+0.013** | +0.065 | **+0.021** |
| 1mo | 356 | +0.081 | **+0.038** | +0.064 | **+0.022** |
| 3mo | 119 | +0.089 | −0.053 | +0.103 | +0.061 |
| 6mo | 59 | +0.234 | −0.113 | +0.176 | −0.038 |
| 1y | 29 | +0.547 | +0.032 | +0.449 | +0.162 |
| 2y | 15 | (표본부족) | — | (표본부족) | — |

- **표본 충분 구간(1d·1w·1mo, n≥356)**: 두 모드 모두 median rankIC ≈ 0 (±0.04). period가 fixed를
  **의미있게 이기지 못함** (1d·1w는 오히려 fixed가 약간 위/period 음수, 1mo만 +0.038 vs +0.022로
  근소하나 노이즈 수준).
- **장기 구간(3mo~1y)의 큰 best_IC**(예: 1y +0.5)는 n=29~59의 **소표본 신기루** — median은 0 근방/음수.
  과거 "2021 h=10 +0.27이 3년서 소멸"과 동일한 함정(하니스 경고 적중).
- baseline(majority/random) 모델은 상수 예측이라 rankIC 정의 불가(nan) — 여기서 유효 대조는 fixed_keyword.

## 결론 (결정적)

**특허 기반 시대별 키워드 + point-in-time 게이트는 고정 키워드 대조군을 어느 충분표본 horizon에서도
이기지 못했다.** 즉 6/23의 무신호는 *"고정 키워드 탓이 아니다"*. 키워드 선택 전략(카테고리 고정 →
시대별 추출)을 바꿔도 DataLab 단독 검색량은 주가 방향 정보를 담지 않는다. **단일 대체데이터(DataLab)
수익률 예측은 — 피벗을 거쳐도 — 확정적으로 종결.**

## 다음 레버

1. **다중소스 후기 융합**(DataLab + 특허 활동 + 채용 수요) — 제품 설계대로 소스별 신호를 결합해 검증.
2. 또는 "수익률 예측"을 접고 제품 실제 목표인 **근거 확인율·방향성**(docs/backtesting-plan.md §6) 평가로 전환.
3. 같은 **단일소스 수익률 예측 실험은 반복하지 말 것**(키워드 전략 변형 포함 — 이번에 닫힘).

## 재현

```bash
cd services/agent-worker
# 처치군
uv run python scripts/sweep_datalab_horizons.py --source period-keyword \
  --feature-mode period_keyword --tickers 005930,000660,035420 \
  --start 2016-01-01 --end 2026-06-22 --benchmark KS11 \
  --prices-csv prices_kospi15_2016_2026.csv \
  --keyword-csv datalab_patent_keywords.csv --keyword-meta "kw_out/patent_keywords_*.json" \
  --out-dir sweep_period
# 대조군: --feature-mode fixed_keyword --out-dir sweep_fixed
```

산출물(CSV/JSON/sweep_*)은 연구용 로컬 아티팩트로 커밋하지 않음. 코드: `app/ml/period_keyword_dataset.py`,
`app/ml/bakeoff.py`(--source period-keyword), `scripts/sweep_datalab_horizons.py`(--source/--feature-mode).
