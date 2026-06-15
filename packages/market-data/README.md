# signal-alpha-market-data

Signal Alpha 공용 **시장 데이터 소스 추상계층**. 소스(키움/토스/DART)별 호출을 하나의
계약 뒤로 숨겨, 메인 서비스 price collector와 `harness/ai_tech_backtest`가 같은 DTO를
공유하게 한다.

## 왜 필요한가 (소스별 역할 분담)

검증 결과(`spikes/toss-feasibility/FINDINGS.md`, 토스 공식 OpenAPI 스펙, `docs/spec/kiwoom-rest-spec.md`):

| 데이터 | 소스 | 비고 |
| --- | --- | --- |
| PER / PBR / EPS / BPS / ROE / ROA | **키움** ka10001 | 토스 Open API는 밸류에이션 전무 |
| 현재가 · OHLC · 시가총액 | 키움 (국내) / 토스 (국내·미국) | |
| 10년 일봉 · 환율 · 미국 시세 | **토스** | 키움은 국내 한정 |
| **PSR** | **파생** (키움 시총 ÷ DART 매출 TTM) | 어느 API도 직접 미제공 |

## 구성

- `contracts.py` — DTO(`ValuationMetrics`, `PriceSnapshot`, `Candle`, `ExchangeRate`)와
  `MarketDataSource` 프로토콜. 순수 계약, I/O 없음. 소스가 미지원하는 메서드는
  `NotImplementedError`.
- `valuation.py` — PER/PBR 패스스루 + **PSR 파생 계산**(`compute_psr`, `build_valuation`).

## PSR 계산 (단위·시간성 주의)

```python
from signal_alpha_market_data import build_valuation

v = build_valuation(
    ticker="005930",
    per=12.5, pbr=1.2,              # 키움 직값 패스스루
    market_cap_eok=5000,           # 키움 시가총액 (억원)
    revenue_ttm_krw_million=200000 # DART 매출 TTM (백만원)
)
v.psr  # Decimal("2.5000")
```

- **단위**: 시총은 억원, 매출은 백만원. `PSR = (시총 × 100) ÷ 매출`. (1 억원 = 100 백만원)
- **시간성**: PSR은 trailing 지표 — 분자(시총)는 실시간, 분모(매출 TTM)는 분기 갱신.
  호출측이 최신 매출 TTM을 캐시하고, `price_snapshots` 갱신마다 PSR을 재계산한다.
- 매출이 없거나 0 이하면 PSR은 `None`.

## 소스 구현체

`MarketDataSource`를 구현하는 `KiwoomSource`/`TossSource`는 httpx 의존이 있어 이 패키지가
아니라 소비처(서비스/하네스)에서 기존 클라이언트(`kiwoom/rest_client.py`,
`spikes/toss-feasibility/toss_client.py`)를 래핑해 구현한다.

## 테스트

```bash
uv run --group dev python -m pytest packages/market-data -q
```
