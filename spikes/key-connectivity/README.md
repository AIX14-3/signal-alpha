# 3사 API 키 연동 스모크 (DART · 키움 · 토스)

세 제공자 키로 가장 싼 엔드포인트를 한 번씩 호출해 **"데이터가 실제로 넘어오는지"**만
빠르게 확인한다. DB·Docker 불필요 (httpx 단독).

## 검증 항목

| 제공자 | # | 항목 | 확인하는 것 |
| --- | --- | --- | --- |
| DART | D1 | `list.json` | 키 유효성(공시 목록 수신) |
| DART | D2 | `fnlttSinglAcntAll.json` | 삼성전자 **매출액**(PSR 분모) 수신 |
| 키움 | K1 | `oauth2/token` | 토큰 발급 |
| 키움 | K2 | `ka10001` | 현재가·**PER·PBR**·시총 수신 |
| 토스 | T1 | `oauth2/token` | 토큰 발급 |
| 토스 | T2 | `api/v1/prices` | 현재가 수신 |
| 토스 | T3 | `api/v1/exchange-rate` | **환율(USD→KRW)** 수신 |

## 실행

1. repo 루트 `.env`에 키를 채운다 (`.env.example` 참고):
   ```
   DART_API_KEY=...
   KIWOOM_APP_KEY=...
   KIWOOM_APP_SECRET=...
   KIWOOM_API_BASE=https://mockapi.kiwoom.com   # 모의. 실전은 https://api.kiwoom.com
   TOSS_CLIENT_ID=...
   TOSS_CLIENT_SECRET=...
   ```
2. 실행:
   ```bash
   uv run --group dev python spikes/key-connectivity/check_keys.py
   ```
3. 콘솔 요약 + `spikes/key-connectivity/FINDINGS.md`(응답 샘플 포함, gitignore됨) 확인.

## 주의

- `.env`/`FINDINGS.md`는 커밋 금지(.gitignore 처리됨). 응답 샘플에 토큰·데이터가 남을 수 있음.
- 키움 모의도메인(`mockapi`)은 시세 범위가 제한될 수 있음 → 값이 비면 실전 키로 재확인.
- D2 매출액은 `bsns_year=2025, reprt_code=11011(연간)` 고정. 보고서 미공시 시기엔 직전 연도로 바꿔 확인.
- 각 시나리오는 격리 실행 — 한 제공자 실패가 다른 결과를 가리지 않는다.
