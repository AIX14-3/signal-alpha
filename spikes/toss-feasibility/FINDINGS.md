# 토스증권 API 타당성 검증 — FINDINGS

- 실행 시각: 2026-06-14T21:35:53
- API base: `https://openapi.tossinvest.com`
- 샘플 종목: KR=`005930`, US=`AAPL`

## 요약

| # | 검증 항목 | 결과 | 소요(ms) | 비고 |
|---|----------|------|----------|------|
| A | 인증 (POST /oauth2/token) | **PASS** | 218 | 토큰 발급 성공 (len=834), 만료 추정 2026-06-15 21:35:45.919609+09:00 |
| B | 국내 실시간 시세 (GET /api/v1/prices) | **PASS** | 63 | params={'symbols': '005930'}, 응답 키=['result'] |
| C | 10년 일봉 깊이 (GET /api/v1/candles, count<=200 → 페이지네이션) ⚠️ 핵심 | **PASS** | 3344 | 총 2600봉 / 13페이지, 가장 과거=2015-11-20T00:00:00.000+09:00, 목표=2016-06-16 → 10년 도달 ✅ |
| D | 미국 시세 (GET /api/v1/prices + /market-calendar/US) | **PASS** | 484 | 미국 종목(AAPL) 시세 조회 성공 params={'symbols': 'AAPL'} |
| E | 환율 (GET /api/v1/exchange-rate) | **PASS** | 281 | USD→KRW 환율 응답 키=['result'] |
| F | 레이트리밋 실측 (prices 연속 10회) | **PASS** | 2610 | 성공 10/10, 429(throttle) 0, 기타오류 0 |

## 상세 응답 샘플

### A. 인증 (POST /oauth2/token) — PASS

토큰 발급 성공 (len=834), 만료 추정 2026-06-15 21:35:45.919609+09:00

```json
{
  "token_prefix": "eyJraWQi…",
  "expires_at": "2026-06-15 21:35:45.919609+09:00"
}
```

### B. 국내 실시간 시세 (GET /api/v1/prices) — PASS

params={'symbols': '005930'}, 응답 키=['result']

```json
{
  "result": [
    {
      "symbol": "005930",
      "timestamp": "2026-06-12T19:59:59.000+09:00",
      "lastPrice": "324500",
      "currency": "KRW"
    }
  ]
}
```

### C. 10년 일봉 깊이 (GET /api/v1/candles, count<=200 → 페이지네이션) ⚠️ 핵심 — PASS

총 2600봉 / 13페이지, 가장 과거=2015-11-20T00:00:00.000+09:00, 목표=2016-06-16 → 10년 도달 ✅

```json
{
  "total_bars": 2600,
  "pages": 13,
  "earliest": "2015-11-20T00:00:00.000+09:00",
  "first_candle_sample": [
    {
      "timestamp": "2026-06-12T00:00:00.000+09:00",
      "openPrice": "313000",
      "highPrice": "339000",
      "lowPrice": "313000",
      "closePrice": "324500",
      "volume": "60357743",
      "currency": "KRW"
    }
  ]
}
```

### D. 미국 시세 (GET /api/v1/prices + /market-calendar/US) — PASS

미국 종목(AAPL) 시세 조회 성공 params={'symbols': 'AAPL'}

```json
{
  "price": {
    "result": [
      {
        "symbol": "AAPL",
        "timestamp": "2026-06-13T08:59:57.000+09:00",
        "lastPrice": "291.5789",
        "currency": "USD"
      }
    ]
  },
  "us_calendar": {
    "result": {
      "today": {
        "date": "2026-06-14",
        "dayMarket": null,
        "preMarket": null,
        "regularMarket": null,
        "afterMarket": null
      },
      "previousBusinessDay": {
        "date": "2026-06-12",
        "dayMarket": {
          "startTime": "2026-06-12T09:00:00.000+09:00",
          "endTime": "2026-06-12T17:00:00.000+09:00"
        },
        "preMarket": {
          "startTime": "2026-06-12T17:00:00.000+09:00",
          "endTime": "2026-06-12T22:30:00.000+09:00"
        },
        "regularMarket": {
          "startTime": "2026-06-12T22:30:00.000+09:00",
          "endTime": "2026-06-13T05:00:00.000+09:00"
        },
        "afterMarket": {
          "startTime": "2026-06-13T05:00:00.000+09:00",
          "endTime": "2026-06-13T08:50:00.000+09:00"
        }
      },
      "nextBusinessDay": {
        "date": "2026-06-15",
        "dayMarket": {
          "startTime": "2026-06-15T09:00:00.000+09:00",
          "endTime": "2026-06-15T17:00:00.000+09:00"
        },
        "preMarket": {
          "startTime": "2026-06-15T17:00:00.000+09:00",
          "endTime": "2026-06-15T22:30:00.000+09:00"
        },
        "regularMarket": {
          "startTime": "2026-06-15T22:30:00.000+09:00",
          "endTime": "2026
… (truncated)
```

### E. 환율 (GET /api/v1/exchange-rate) — PASS

USD→KRW 환율 응답 키=['result']

```json
{
  "result": {
    "baseCurrency": "USD",
    "quoteCurrency": "KRW",
    "rate": "1520.3",
    "midRate": "1519.8",
    "basisPoint": "3",
    "rateChangeType": "EQUAL",
    "validFrom": "2026-06-14T21:35:06.000+09:00",
    "validUntil": "2026-06-14T21:40:05.000+09:00"
  }
}
```

### F. 레이트리밋 실측 (prices 연속 10회) — PASS

성공 10/10, 429(throttle) 0, 기타오류 0

```json
{
  "errors": []
}
```

## go / no-go 판단

> 아래는 결과를 보고 사람이 채운다.

- [ ] **C(10년 일봉 깊이)** 가 10년 또는 페이징으로 도달 가능한가? → 백필 실현성
- [ ] B/D 응답 필드가 기존 `StockSnapshot`(price-collector)에 매핑되는가?
- [ ] D 미국 심볼 체계·통화가 스키마 확장(exchange/currency/country)과 맞는가?
- [ ] F 레이트리밋이 전체 유니버스 폴링에 충분한가?

**결론(go/no-go):** _____

### go일 경우 후속 본구현 범위 (별도 작업)
- 브로커 추상화 계층(`DataSourceCollector` 프로토콜) → Kiwoom/Toss 교체
- 스키마 확장: `stocks`에 exchange/currency/country, `market` CHECK에 US 거래소
- FX 테이블, 미국 종목 수집기, 10년 백필 잡, LLM 프롬프트 다중시장 변형