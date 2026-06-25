# 외부 어텐션 소스 + 종목 집계 검색량 검증 (Stage 8)

**날짜**: 2026-06-25
**worktree/브랜치**: `sa-ml-longhorizon` / `feat/ml-datalab-longhorizon`
**선행**: Stage6(주간 동시·예측0), Stage7(일간 동시·발행지연=사후값)

두 가지를 추가 검증한다. **(실험1)** DataLab만의 문제인가? → 외부 어텐션 소스(en.Wikipedia)를
같은 일간 하니스로. **(실험2)** 사건이 아니라 **종목 단위 집계 검색량의 추세**(주/월/분기/년 평균
증감)가 주가에 영향을 주는가? → 연속 시계열로 DataLab 재실험.

---

## 실험 1 — en.Wikipedia 일간 조회수 리드-래그 (외부 소스)

en.Wikipedia 일간 조회수(절대 카운트, 무료·키없음)를 3종목 article에 붙여 **Stage7과 동일한
`event_study_daily.py`** 로 실행(코드 무변경). 24사건 중 23 수집(Naver 2022-10 윈도 404).

| | 피크 | day−1(전일·보유) | day 0(당일) | Δ_lag | lag0 corr | lag+1 corr |
|---|---|---|---|---|---|---|
| **Wikipedia** | **day 0 (동시)** | −0.06 | +0.74 | +0.80 | +0.098 | +0.100 |
| (참고)DataLab | day 0 (동시) | −0.06 | +1.29 | +1.34 | +0.071 | −0.049 |

→ **외부 소스도 변동 당일(0)에 동시 피크, 선행 빌드업 없음.** Wikipedia의 lag+1=+0.10은 DataLab보다
살짝 양수지만 n=23서 노이즈 수준(무의미). **즉 동시·비선행은 DataLab 고유 결함이 아니라 이 대형주들에
대한 어텐션 데이터의 일반 속성**(학술증거 [[attention-lead-lag-evidence]]와 일치).

## 실험 2 — 종목 집계 검색량 다중 horizon (검색 증감 → 수익)

종목 단위 검색지수 2종을 만들어 주/월/분기/년 평균의 **증감(level 아닌 change)** 이 수익과 어떤
관계인지 본다(level은 둘 다 우상향이라 허위상관 → 반드시 change로). corr_predict=검색증감↔**미래**수익,
corr_coincide=↔**동시**수익. 3종목 pooled.

**stockname (종목명 검색 = 삼성전자/SK하이닉스/네이버):**

| horizon | n_indep | corr_predict (미래) | corr_coincide (동시) |
|---|---|---|---|
| 1주 | 1626 | −0.014 | +0.158 |
| 1개월 | 402 | +0.011 | +0.149 |
| 1분기 | 118 | +0.063 | +0.186 |
| 1년 | 24 | +0.016 | **+0.217** |

→ **검색량 증감은 주가와 "동시에" 움직이지만(corr_coincide +0.15~+0.22, horizon 길수록 강화),
미래 수익은 예측 못 함(corr_predict ≈ 0, 전 horizon).** composite(키워드 풀 평균)는 테크용어 노이즈라
predict·coincide 모두 ≈0. 장기 horizon 큰 spread(연 +28%)는 n_indep=24 소표본 신기루.

동시 상관(+0.2)은 **역인과 가능성**(주가·뉴스가 검색을 유발)을 포함 — Stage7 일간 결과(검색이 변동
당일에만 튐, 이전엔 0)가 "검색이 가격을 반영하는 반응형"임을 이미 보였다.

## 종합 결론

세 독립 각도가 **하나로 수렴**한다:
1. 사건별 일간(Stage6/7): 검색은 변동 당일 동시 피크, 발행지연으로 받는 값은 사후값.
2. 외부 소스(Wikipedia): 다른 데이터·절대카운트·글로벌 관심이어도 **여전히 동시·비선행**.
3. 집계 다중 horizon: 주~년 어느 평균에서도 검색 증감은 수익과 **동시(+0.2)일 뿐 예측 0**.

→ **검색/어텐션 데이터(DataLab·Wikipedia 공통)는 대형주에 대해 동시·반응형 지표다. 줄거나 오르면
주가와 *같이* 움직이지만 주가를 *앞서지* 않는다.** 단독 선행 예측은 소스·집계·해상도를 바꿔도 불가.
용도는 **동시 확인(근거·관심 쏠림)·변동성/거래량 나우캐스팅·다중소스 보조 피처**. 방향 선행은
구조적으로 다른 소스(수급 KRX 투자자별 거래·채용·특허)에서 찾아야 한다.

## 재현

```bash
cd services/agent-worker
# 실험1
uv run python scripts/collect_wikipedia_event_windows.py --events events_with_moveday.json --out wiki_event_daily.csv
uv run python scripts/event_study_daily.py --events events_with_moveday.json \
  --daily-csv wiki_event_daily.csv --prices-csv prices_kospi15_2016_2026.csv --benchmark KS11 --k 10
# 실험2
uv run python scripts/collect_datalab_for_keywords.py --kw-dir stockname_kw --out stockname_datalab.csv --time-unit week
uv run python scripts/aggregate_search_momentum.py --keyword-csv datalab_patent_keywords.csv \
  --stockname-csv stockname_datalab.csv --prices-csv prices_kospi15_2016_2026.csv --benchmark KS11
```

코드: `scripts/{collect_wikipedia_event_windows,aggregate_search_momentum}.py`(+Stage7 하니스 재사용).
데이터/CSV는 로컬 아티팩트로 커밋하지 않음. 관련: [[ml-bakeoff-datalab-result]], [[attention-lead-lag-evidence]].
