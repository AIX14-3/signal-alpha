# 횡단면 어텐션 반전 + 16모델 ML (Stage 10, 실험 1+2)

**날짜**: 2026-06-25
**worktree/브랜치**: `sa-ml-longhorizon` / `feat/ml-datalab-longhorizon`
**선행**: Stage5~9 — 검색은 *시계열*로 방향 예측 0, 변동성/거래량엔 동시 신호. 미검증 각도 = **횡단면·반전·다종목**(어텐션 효과의 정통 형태, Da/Engelberg/Gao 2011).

## 질문

지금까지 전부 **시계열(3종목)**. Da et al의 정통 효과는 **횡단면 반전**: "비정상 관심↑ 종목은 단기 과열 후 *언더퍼폼*". 선형 시계열 corr은 이를 평균으로 지움. **KOSPI 15종목**으로 native form 검증 + **16모델 bake-off**도 함께.

## 셋업

- **유니버스**: 15종목(삼성·하이닉스·네이버·기아·현대차·카카오·셀트리온·삼성바이오·크래프톤·하이브·유한양행·에스엠·한미반도체·HL만도·스튜디오드래곤). 종목명 검색 주간(15개 추가 수집) + 가격(보유).
- **예측자(PIT)**: abnormal 관심 = 종목명 검색 LEVEL의 트레일링 26주 롤링 z.
- **타깃**: 각 주 t, 각 종목의 H주(1/2/4주) 수익에서 **그 주 15종목 평균수익을 뺀 횡단면 초과수익**.
- **테스트**: (1) 횡단면 IC = 주별 corr(관심, 미래 횡단면초과)의 평균 + t-stat. 음수=반전.
  (2) 포트폴리오: 저관심−고관심 분위 미래수익(양수=반전이 이득). (3) 꼬리(z>2) 미래수익.
  (4) **16모델 bake-off**(rows=(종목,주), X=[abn,모멘텀,level], y=초과 방향, 워크포워드).

## 결과

| horizon | weeks | 횡단면 IC | IC_t | 저-고_fwd% | tail(z>2)_fwd% |
|---|---|---|---|---|---|
| 1주 | 532 | +0.004 | +0.27 | −0.232 | +0.074 |
| 2주 | 531 | +0.000 | +0.01 | −0.465 | +0.145 |
| 1개월 | 529 | +0.009 | +0.70 | −0.994 | +0.315 |

**16모델 bake-off**(횡단면 abnormal 관심 → 1개월 방향, 6740샘플, up-rate 0.45):
- 최고 rankIC **+0.019**(decision_tree), stacking +0.011 — baseline_stratified(+0.009) 수준, **baseline 못 이김**.
- 선형(logistic/lda/ridge) rankIC −0.047~−0.048(약한 반전 기미지만 미미). sd_IC≈mean → 무신호.

## 결론

- **횡단면 IC ≈ 0, t<1 → 반전도 모멘텀도 없음.** 꼬리 극단관심은 오히려 약한 *연속*(+0.07~0.32%)이나 비유의.
- **16모델 ML도 rankIC≈0**(Stage 5와 동일) — 횡단면 관심으로 방향 예측 불가.
- **Da et al 반전 효과 부재.** 학술 caveat대로 그 효과는 **소형·리테일·차익거래難 종목**에 집중하는데, 이 15개는 **대형·유동성 종목**이라 부재. 즉 "1%"는 이 유니버스엔 없음 — *유니버스를 바꿔야* 한다.

→ 방향 신호 탐색의 결론: **대형주에선 어떤 어텐션 framing(시계열·횡단면·반전·16모델)으로도 0.** 남은 진짜 후보는 **(a) 소형 KOSDAQ 유니버스**(반전 효과의 서식지), **(b) 검색→펀더멘털→주가**(제품 가설), **(c) 수급 등 비어텐션 선행 소스**.

## 한계

- 일부 종목 상장 늦음(크래프톤 2021·하이브 2020·삼성바이오 2016) → 횡단면 표본 주별 6~15개 변동.
- 종목명 단일 키워드(브랜드) — 투자의도 키워드("X 주가/매수")는 미검증(내일 #3).
- 대형주 한정. 소형주 미검증(내일 유니버스 변경).

## 재현

```bash
cd services/agent-worker
uv run python scripts/collect_datalab_for_keywords.py --kw-dir stockname15_kw --out stockname15_datalab.csv --time-unit week
uv run python scripts/cross_sectional_attention.py --prices-csv prices_kospi15_2016_2026.csv \
  --tickers 000100,000270,000660,005380,005930,035420,035720,041510,042700,068270,204320,207940,253450,259960,352820
```

코드: `scripts/cross_sectional_attention.py`. 데이터/CSV 커밋 제외. 관련: [[ml-bakeoff-datalab-result]], [[attention-lead-lag-evidence]]. 내일 계획: `docs/experiments/2026-06-26-attention-followups-plan.md`.
