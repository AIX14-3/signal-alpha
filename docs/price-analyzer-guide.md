# PRICE(주가) 분석기 — 팀 공유 가이드

> 한 줄 요약: **키움에서 수집해 둔 주가·수급 데이터를 읽어서, 다른 소스(DART 등)와 똑같은 형식의 "방향 + 점수"로 바꿔주는 분석기**를 agent-worker에 추가했습니다.

## 1. 전체 그림 — 우리 시스템의 4단계

```text
[수집기] → [분석기] → [토론(합산)] → [점수]
```

| 단계 | 하는 일 | 비유 |
| --- | --- | --- |
| ① 수집기 (Collectors) | 외부에서 원본 데이터를 가져온다 (DART 공시, 키움 시세, 채용공고 …) | 재료 사오기 |
| ② 분석기 (Analyzers) | 소스별로 "긍정/부정/중립 + 점수 + 근거"를 만든다 | 재료 손질 |
| ③ 토론 (Orchestrator) | 소스별 결과를 한자리에 모아 비교·합산한다 | 요리사들 회의 |
| ④ 점수 (Final Signal) | 최종 점수와 근거를 DB에 저장, 사용자에게 보여준다 | 완성된 요리 |

이번 작업은 **②번 자리에 "주가 분석기"를 새로 채워 넣은 것**입니다. (간략 그림: `docs/superpowers/plans/2026-06-11-signal-flow-overview.svg`)

## 2. 이번에 추가된 것

| 파일 | 역할 |
| --- | --- |
| `agent-worker/app/collectors/price/ohlcv_reader.py` | DB의 `ohlcv_data`에서 최근 120영업일 주가·수급을 읽어온다 |
| `agent-worker/app/analyzers/price/indicators.py` | 이동평균, RSI, 거래량 급증, 외인·기관 연속 순매수 같은 지표 계산 |
| `agent-worker/app/analyzers/price/rules.py` | 지표를 점수로 변환하는 규칙 (LLM 없음, 항상 같은 입력 → 같은 결과) |
| `agent-worker/app/analyzers/price/analyzer.py` | 위 둘을 묶어 표준 결과(`SourceResult`)로 포장 |
| `agent-worker/app/api/routes/price.py` | `POST /internal/price/analyze/{종목코드}` 엔드포인트 |

## 3. 자주 나올 질문

**Q. 분석기가 키움 API를 직접 부르나요?**
아니요. 키움 호출은 `services/price-collector`(수집기)만 합니다. 분석기는 수집기가 DB에 쌓아둔 `ohlcv_data`를 **읽기만** 합니다. 그래서 키움 장애·인증 문제와 분석 로직이 서로 분리됩니다.

**Q. 왜 이름이 KIWOOM이 아니라 PRICE인가요?**
데이터의 본질이 "주가·수급"이기 때문입니다. 나중에 수집처를 다른 증권사로 바꿔도 분석기는 그대로 쓸 수 있습니다.

**Q. 점수는 어떻게 읽나요?**
`-1.0 ~ +1.0` 범위입니다. +면 상승 신호 요소가 많고, -면 하락 신호 요소가 많다는 뜻입니다.
- 추세 (이동평균 정배열/역배열, 골든·데드크로스): 최대 ±0.45
- 모멘텀 (RSI): ±0.1
- 수급 (외인·기관 연속 순매수/순매도): 최대 ±0.35
- 추세와 수급이 서로 반대로 강하면 `mixed`(엇갈림)로 표시

**Q. 데이터가 없거나 오래되면요?**
- 데이터 없음 → `data_status: failed`
- 최신 거래일이 7일 이상 지남 → `stale_data` 플래그 + `partial`
- 21영업일 미만 → 점수를 내지 않고 `insufficient_history`

**Q. LLM을 쓰나요?**
안 씁니다. 전부 수식 기반 규칙이라 결과가 재현 가능하고 테스트로 검증됩니다 (신규 테스트 30여 개).

## 4. 직접 돌려보기

```powershell
# 테스트
cd services/agent-worker
uv run pytest -q tests/test_price_indicators.py tests/test_price_rules.py tests/test_price_analyzer.py

# 서버 띄우고 호출 (DATABASE_URL 필요)
uv run uvicorn app.main:app --reload --port 8011
# POST http://localhost:8011/internal/price/analyze/005930
```

## 5. 다음 단계

1. REST 실시간 수집기(`feat/kiwoom-rest-realtime-collector`)가 머지되면 실데이터로 검증 — 단 과거 120일 백필 전까지는 누적 일수 부족으로 `insufficient_history`가 정상
2. 최종 합산(D-1) 가중치에 PRICE를 몇 %로 넣을지 팀 결정
3. 별도 브랜치에서 "예측 점수 vs 실제 주가" 검증 파이프라인 구축 (백테스트)

상세 설계 근거는 `docs/superpowers/plans/2026-06-11-kiwoom-price-analyzer.md`, 구조도는 같은 폴더의 `.svg` 참고.
