# AI 선도주 기술적분석 예측 적중률 백테스트

AI 선도 상장주(~12종목)에 대해 **차트봉 + Stochastic·StochRSI·OBV·MACD → 알고리즘 정제
→ LLM 판단**의 방향 예측 적중률을, 익일·1주·1개월 / 전체 10년·AI 국면(2~3년)으로 나눠
**out-of-sample(워크포워드)**로 정직하게 측정한다.

> 목적은 "예측기"가 아니라 **누수 차단 + 베이스라인 대비 측정**. 익일 적중률은 잘해야
> ~52~55%가 정상이며, 60%+가 나오면 누수/과적합을 의심해야 한다. 실거래·자동매매는 범위 밖.

## 대상
- US: MSFT, GOOGL, AMZN, META, NVDA, AMD, INTC, AVGO, ORCL, TSM(TSMC ADR)
- KR: 삼성전자(005930), SK하이닉스(000660)
- 제외: OpenAI·Anthropic(비상장), Google=Alphabet(GOOGL로 통합)

## 실행 (harness venv)
```bash
# 1) .env(레포 루트)에 토스 키 필요: TOSS_CLIENT_ID / TOSS_CLIENT_SECRET
#    (LLM 비교를 원하면 GEMINI_API_KEY 또는 OPENAI_API_KEY도)

# 2) 데이터 적재 → harness/ai_tech_backtest/data/ohlcv/*.parquet
../.venv/Scripts/python.exe run.py --ingest

# 3) 백테스트 + 리포트
../.venv/Scripts/python.exe run.py --backtest
# LLM 판단 비교까지:
../.venv/Scripts/python.exe run.py --backtest --llm
```

## 산출물
- `REPORT.md` — 기간별·국면별·종목별 적중률, 베이스라인 대비 리프트, placebo, LLM 비교, 결론
- `charts/*.png` — 기간별/국면별 적중률, 누적수익(참고), 종목별 적중률

## 모듈
| 파일 | 역할 |
|------|------|
| `universe.py` | 12종목 정의(비상장 제외) |
| `ingest.py` | 토스 candles 재사용(`spikes/toss-feasibility/toss_client.py`)해 10년 일봉 적재 |
| `indicators.py` | MACD·Stochastic·StochRSI·OBV·RSI·캔들봉 피처(인과적) |
| `labeling.py` | 다중 기간 forward 라벨(데드존, 누수 차단) |
| `signals.py` | 규칙 기반 신호 + 옵션 ML(로지스틱, 폴드 내 학습) |
| `backtest.py` | 워크포워드 OOS 엔진 + placebo 누수검증 |
| `metrics.py` | 적중률·F1·베이스라인 리프트·누적수익 |
| `llm_judge.py` | as-of 스냅샷 LLM 판단 vs 규칙(동일 표본) |
| `charts.py` / `report.py` / `run.py` | 시각화·리포트·오케스트레이션 |

## 주의
- 연구용 모듈로 운영 DB/스키마를 건드리지 않고 로컬 parquet만 사용.
- 결과는 분석 방법론 검증용이며 투자 자문이 아니다. 상관성↑·선택편향 한계를 리포트에 명시.
