# DataLab 발행지연(전일확정) 검증 (Stage 7) — 당일 vs 하루 뒤

**날짜**: 2026-06-25
**worktree/브랜치**: `sa-ml-longhorizon` / `feat/ml-datalab-longhorizon`
**선행**: Stage6(주간 이벤트 스터디 — 검색은 변동과 동시·전구간 예측상관≈0)

## 질문 (사용자 지적)

Stage6는 "검색이 가격과 **동시(coincident)**"라 했다. 그런데 **DataLab 일간 검색지수는 "전일확정"** —
어떤 날 D의 검색값은 **D+1에야** 받을 수 있다(Naver API는 어제까지만 반환, `naver_datalab_client.py`
기본 end=`today-1`). 그렇다면:

1. 검색이 동시라면 **받는 데이터는 주가가 이미 움직인 뒤의 값**인가?
2. **"하루 뒤 값"과 "당일 값"의 차이**는 얼마인가?

주간 분석으론 1일 효과를 못 보므로 **일간** 해상도로 재검증.

## 셋업

1. **정확한 변동 DAY**(`detect_event_move_day.py`): 24개 사건 각 ISO주에서 |일간 초과수익| 최대 거래일.
2. **일간 윈도 수집**(`collect_datalab_daily_windows.py`): 사건 키워드를 `[move_day±15일]`(end는 어제로
   클램프) **일간** 수집 → 22/24 데이터(2개 niche 키워드 전무). prod 쓰기 0.
3. **분석**(`event_study_daily.py`): (A) move_day=0 기준 달력일 z-프로파일, (B) 일간 교차상관.

## 결과

### (A) 일간 검색 프로파일 (z-score, move_day=0, 22사건)

| offset(일) | −2 | **−1 (전일·보유)** | **0 (당일·변동)** | **+1 (하루 뒤)** | +2 |
|---|---|---|---|---|---|
| mean z | −0.07 | **−0.06** | **+1.29** | **+0.28** | +0.15 |

→ 검색은 변동 **당일(0)에만 급등**(z≈+1.3), 그 **이전 날들은 전부 ≈0**(선행 빌드업 없음=엄격 동시).
주간 결과의 완만한 −1/−2 상승은 주간 버킷이 당일 스파이크를 뭉갠 착시였음(일간은 깔끔히 동시).

- **Δ_lag = z(0) − z(−1) = +1.34**. 변동 당일 행동 시 *실제 보유값*(전일, −0.06)엔 스파이크가 **전혀 없다**.
  스파이크 전부가 **아직 못 받은 당일값**에 갇혀 있다 → **받는 데이터(D값을 D+1에 수령)는 100% 사후값**.

### (B) 일간 교차상관 — 발행지연 비용 (검색모멘텀 vs 초과수익)

| lag(일) | … | **0 (동시·실시간 가정)** | **+1 (발행지연 후 현실)** | … |
|---|---|---|---|---|
| corr | | **+0.071** | **−0.049** | |

→ 작은 동시 상관(+0.071)조차 **발행지연을 지나면 사라진다**(현실 거래 셀 lag+1 = −0.049 ≈ 0). 멀리 떨어진
lag(+4 등)의 값은 표본·메커니즘 없는 노이즈.

## 결론 (사용자 질문에 직접 답)

1. **"받는 데이터가 사후값인가?" → 그렇다(확정).** 검색은 변동 **당일에만** 튀고(엄격 동시), 일간 데이터는
   전일확정이라 그 값을 **다음날에야** 받는다. 변동 시점에 **보유한 전일값엔 정보가 없다(Δ_lag=+1.34)**.
2. **"하루 뒤 vs 당일 차이" → 극단적.** 당일값(+1.29σ) vs 전일보유값(−0.06σ)은 ~1.3σ 격차지만, 그 큰
   당일값은 **변동 후에 도착**해 행동 불가. 게다가 그것을 *예측*에 써도(lag+1) 상관 ≈0이라 **늦게 받은 값도
   예측가치 없음**.

→ **DataLab은 동시·사후 확인 지표**다. 발행지연까지 감안하면 단독 선행 예측은 **구조적으로 불가**.
제품 설계대로 **근거·흔적 확인**(사건 실재·관심 쏠림의 사후 확증)과 **다중소스 융합 보조 피처**로만 타당.
(나우캐스팅도 '당일값을 당일에 못 받으므로' 실시간 용도엔 제약.)

## 재현

```bash
cd services/agent-worker
uv run python scripts/detect_event_move_day.py --events events_with_keywords.json \
  --prices-csv prices_kospi15_2016_2026.csv --benchmark KS11 --out events_with_moveday.json
uv run python scripts/collect_datalab_daily_windows.py --events events_with_moveday.json \
  --out event_daily_datalab.csv --window-days 15
uv run python scripts/event_study_daily.py --events events_with_moveday.json \
  --daily-csv event_daily_datalab.csv --prices-csv prices_kospi15_2016_2026.csv --benchmark KS11 --k 10
```

코드: `scripts/{detect_event_move_day,collect_datalab_daily_windows,event_study_daily}.py`. 데이터/JSON/
CSV는 로컬 아티팩트로 커밋하지 않음. 관련: 2026-06-25-datalab-event-leadlag.md, [[ml-bakeoff-datalab-result]].
