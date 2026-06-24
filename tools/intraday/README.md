# intraday capture — 장중 고빈도 시세 적재 (백테스트용)

삼성전자(005930)·SK하이닉스(000660)의 장중 데이터를 **1분 주기**로 폴링해
**원본 JSONL**로 적재한다. 파일은 **30분 단위로 로테이션**된다.

## 수집 5종 / 소스 (하이브리드)

| 데이터 | 소스 | 엔드포인트 / TR | 비고 |
|---|---|---|---|
| 가격(현재가) | 토스 | `GET /api/v1/prices` (`symbols=005930,000660`) | 멀티심볼 1콜 |
| 거래량(체결 틱) | 토스 | `GET /api/v1/trades` (`symbol=`) | 종목별 |
| 호가 잔량(슬리피지) | 토스 | `GET /api/v1/orderbook` (`symbol=`) | asks/bids 10단계 price+volume(잔량) |
| 투자자별 매매동향(외국인/기관) | 키움 | `ka10059` (`/api/dostk/stkinfo`) | 장중은 추정치, 확정은 마감 후 |
| 프로그램 매매 | 키움 | `ka90004` 종목별프로그램매매현황 | 시장 top-50 1콜 → 전처리에서 두 종목 추출 |

> 토스 API에는 프로그램매매·투자자동향이 없어 그 둘만 키움으로 보완.

## 적재 경로
```
data/intraday/<YYYYMMDD>/<source>_<kind>_<HHMM>.jsonl
  예: toss_price_0900.jsonl, toss_orderbook_0930.jsonl,
      kiwoom_investor_1000.jsonl, kiwoom_program_1430.jsonl
```
각 줄: `{captured_at, source, kind, symbol, raw}` — `raw`는 API 원본 응답 그대로.

## 실행

검증(드라이런, 1사이클 + 진단):
```bash
KIWOOM_REST_BASE_URL=https://mockapi.kiwoom.com KIWOOM_TR_DELAY_SEC=0.8 \
  .venv/Scripts/python.exe tools/intraday/capture.py --once \
  --program-api-id ka90004 --program-body '{"mrkt_tp":"P00101","amt_qty_tp":"1","stex_tp":"1"}'
```

장중 가동(09:00–15:40, 1분 주기):
```bash
PYTHONIOENCODING=utf-8 KIWOOM_REST_BASE_URL=https://mockapi.kiwoom.com KIWOOM_TR_DELAY_SEC=0.8 \
  .venv/Scripts/python.exe tools/intraday/capture.py \
  --program-api-id ka90004 --program-body '{"mrkt_tp":"P00101","amt_qty_tp":"1","stex_tp":"1"}' \
  --interval 60 --open 09:00 --close 15:40 > tools/intraday/capture.log 2>&1 &
```
- `--ignore-market` : 시간 게이트 무시하고 즉시 수집(테스트용).
- `--interval 30`  : 주기를 30초로(더 고빈도). 기본 60초=1분.

## 중요 메모 / 함정
- **키움 키는 모의(mock) 키** → `mockapi.kiwoom.com` 사용 필수. 실전 도메인은 8030 에러.
  모의 도메인이라도 시세·호가·투자자·프로그램매매는 **실데이터**.
- 토스 `/orderbook`·`/trades` 파라미터는 **`symbol`(단수)**, `/prices`만 `symbols`(콤마 리스트).
- `ka90004`는 stk_cd로 필터되지 않고 **시장 top-50**을 반환 → 대형주(삼성·SK하이닉스)는 항상 포함.
- Windows 콘솔(cp949) 때문에 로그 print는 ASCII만. `PYTHONIOENCODING=utf-8` 권장.
- 모의 도메인 레이트리밋 → 키움 호출 간격 `KIWOOM_TR_DELAY_SEC=0.8` (0.2는 429 발생).

## 클라이언트 출처
`toss_client.py`는 `spike/toss-api-feasibility`의 검증된 PoC, `kiwoom_client.py`는
레포 price-collector(`app/collectors/price/kiwoom`)를 self-contained로 벤더링한 것(app 패키지·DB 의존 회피).
