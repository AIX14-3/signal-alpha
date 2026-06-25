# 타깃 변경: 검색 → 변동성/거래량 (Stage 9)

**날짜**: 2026-06-25
**worktree/브랜치**: `sa-ml-longhorizon` / `feat/ml-datalab-longhorizon`
**선행**: Stage5~8 — 검색은 수익 **방향**을 예측 못 함(동시·반응형). 학술증거 [[attention-lead-lag-evidence]]:
어텐션은 **2차 모멘트(변동성·거래량)** 를 설명하지 방향이 아님(Vlastakis&Markellos: 검색이 VIX 변동
~50% 설명).

## 질문

예측 **타깃을 바꾼다**: 방향(direction) 대신 **미래 변동성·거래량**을 같은 DataLab 검색으로 예측 가능한가?

## 셋업

- **예측자(PIT·과거만)**: ABNORMAL 검색 = 종목 주간 검색 LEVEL의 **트레일링 26주 롤링 z-score**
  ("지금 관심이 비정상적으로 높은가"). 2종: 종목명 검색(삼성전자/SK하이닉스/네이버), 키워드풀 composite.
- **타깃(주간 horizon H=1주/1개월/1분기, 3종목 pooled)**:
  - `vol(미래)` 다음 H주 일간수익 연율화 realized vol / `vol(동시)` 직전 H주
  - `vol증분` = vol(미래) − vol(동시) (현재 변동성 군집성 제거 → 진짜 선행 증분)
  - `volume(미래)` 다음 H주 평균 로그거래량 − 트레일링52주 (abnormal volume)
  - `return(방향)` 다음 H주 수익 (대조군)
- 데이터: 일간 종가+거래량 pykrx(2568거래일, OHLCV는 로그인 불필요·작동), DataLab 주간(기존).
  변동성은 추세 약해 level 사용, 검색은 롤링 z로 정상화. **changes 위주, level-trend 허위상관 방지.**

## 결과 (stockname 종목명 검색)

| horizon | n_indep | vol(미래) | vol(동시) | **vol증분** | volume(미래) | return(방향) |
|---|---|---|---|---|---|---|
| 1주 | 1020 | +0.263 | +0.426 | **−0.171** | +0.253 | −0.005 |
| 1개월 | 396 | +0.212 | +0.229 | +0.001 | +0.145 | +0.026 |
| 1분기 | 120 | +0.155 | +0.109 | +0.086 | +0.107 | +0.101 |

composite(키워드풀)는 ~0(테크용어 노이즈) — 의미있는 건 종목명 검색.

## 해석 (정직하게)

1. **타깃을 바꾸니 신호가 나타난다.** abnormal 검색은 변동성·거래량과 **분명한 양의 상관(+0.2~0.43)**,
   방향(≈0)과 확연히 다름. → "검색이 무신호"가 아니라 **무엇을 묻느냐의 문제**였다(학술과 일치).
2. **그러나 대부분 "동시(coincident)".** `vol증분`(미래−동시)이 1주 −0.17(검색은 변동성 *정점*에 같이
   튀고 이후 평균회귀), 1개월 ≈0, 1분기만 +0.09로 약한 선행. 즉 검색이 변동성을 *앞서* 끌어올리는 게
   아니라 **변동성·관심이 함께 높은** 것 → Stage6~8의 "코인시던트" 결론과 완전 일관.
3. **거래량**: 미래 +0.11~+0.25 양의 상관(군집성 포함이나 나우캐스팅엔 유효).

## 결론

**검색/어텐션 데이터의 진짜 쓸모 = 방향 예측기가 아니라 "변동성·거래량·관심 나우캐스팅(리스크 플래그)".**
abnormal 종목명 검색이 높으면 → 그 종목은 **지금~가까운 미래에 변동성·거래량이 높다**(주로 동시, 약한
선행). 방향은 못 부르지만, **"이 종목 지금 시끄럽다 → 변동성/유동성 주의"** 신호로는 분명히 작동한다.

→ 제품 함의: DataLab은 final_signal의 **방향 점수**가 아니라 **관심/리스크 컨텍스트(변동성·거래량 경보,
근거 쏠림 확인)** 로 쓰는 게 데이터 특성에 맞다(docs/project-context.md §9/§10 흔적·주의근거와 정합).
선행 *방향* 알파는 여전히 구조적으로 다른 소스(수급·채용·특허)에서 찾아야 함.

## 한계

- `vol(미래)`엔 변동성 군집성이 큼 → 순수 선행은 `vol증분`으로 봐야 하고 그건 작음(정직히 명시).
- 3종목 pooled·overlapping window라 장기 horizon n_indep 작음(1분기=120). 종목 확대 시 재검증 권장.
- 동시 상관은 역인과(가격·뉴스→검색) 포함.

## 재현

```bash
cd services/agent-worker
uv run --with pykrx python scripts/collect_ohlcv_pykrx.py --tickers 005930,000660,035420 \
  --start 2016-01-01 --out daily_ohlcv.csv
uv run python scripts/search_target_change.py --keyword-csv datalab_patent_keywords.csv \
  --stockname-csv stockname_datalab.csv --ohlcv-csv daily_ohlcv.csv
```

코드: `scripts/{collect_ohlcv_pykrx,search_target_change}.py`. 데이터/CSV 커밋 제외. 관련:
[[ml-bakeoff-datalab-result]], [[attention-lead-lag-evidence]], 2026-06-25-attention-source-and-aggregate.md.
