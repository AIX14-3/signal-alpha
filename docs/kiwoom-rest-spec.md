# Signal α — 키움 REST API 수집 데이터 명세 (Price Collector)

> 키움증권 **REST API** (App Key/Secret + OAuth) 기준 — `api.kiwoom.com` / 모의 `mockapi.kiwoom.com`
> 원본 명세(OpenAPI+/pykiwoom 기준, 2026-06-08)를 REST 기준으로 변환. 작성일: 2026-06-11

## 0. 수집 범위 구분 (중요)

원본 명세의 TR 중 **시점 스냅샷성 데이터만 실시간(폴링) 수집 대상**이다.
차트(기간) 데이터는 본질적으로 과거 누적 조회라 실시간 수집과 분리하며, **후속 작업**으로 미룬다.

| 구분 | TR (OpenAPI+) | REST api-id | 상태 |
| --- | --- | --- | --- |
| ✅ 실시간 폴링 (장중 60초) | OPT10001 주식 기본 정보 | `ka10001` | **구현됨** |
| ✅ 장 마감 후 1회 | OPT10059 투자자 매매동향 | `ka10059` | **구현됨** |
| ⏳ 후속 (확인 필요) | OPT20004 업종별 현재 시세 | `ka20004`(추정) | 업종 TR의 REST 대응 여부 키움 문서 확인 필요 |
| ⏳ 후속 (기간 백필) | OPT10081 일봉 (120일) | `ka10081` | 과거 백필 — 별도 브랜치 |
| ⏳ 후속 (기간) | OPT10082~10084 주/월/년봉 | `ka10082`~`ka10084` | 장기 추세 분석용 |
| ⏳ 후속 (기간) | OPT20006 업종 일봉 | `ka20006`(추정) | 업종 상대강도용 |

## 1. 인증

```text
POST {base}/oauth2/token
{ "grant_type": "client_credentials", "appkey": ..., "secretkey": ... }
→ token (만료 전 자동 갱신, app/kiwoom/auth.py)
```

- 현재 발급 키는 **모의투자용 (만료 2026-09-06)** → `mockapi.kiwoom.com`.
- 모의 도메인은 시세 범위 제한 가능 → 실데이터 적재 시 실전 키 발급 필요.

## 2. ka10001 · 주식 기본 정보 (실시간 폴링)

```text
POST {base}/api/dostk/stkinfo
headers: authorization: Bearer {token}, api-id: ka10001
body: { "stk_cd": "005930" }
```

| 명세 항목 | 단위 | REST 필드(매핑 상수) | 적재 컬럼 (`price_snapshots`) |
| --- | --- | --- | --- |
| 현재가 | 원 | `cur_prc` | `current_price` |
| 시가 / 고가 / 저가 | 원 | `open_pric` / `high_pric` / `low_pric` | `open` / `high` / `low` |
| 거래량 | 주 | `trde_qty` | `volume` |
| 거래대금 | 백만원 | `trde_prica` | `trade_value` |
| 시가총액 | 억원 | `mac` | `market_cap` |
| 상장주수 | 천주 | `flo_stk` | `shares_outstanding` |
| PER / PBR | 배 | `per` / `pbr` | `per` / `pbr` |
| EPS / BPS | 원 | `eps` / `bps` | `eps` / `bps` |
| ROE / ROA | % | `roe` / `roa` | `roe` / `roa` |

- 매핑 상수: `services/price-collector/app/collectors/stock_snapshot.py` `KA10001_FIELDS`
- 응답 숫자는 `+74300` 형태의 부호 접두 문자열 → 가격류는 절대값 파싱 (`app/kiwoom/parsing.py`)
- 당일 OHLC + 현재가(종가 취급)는 `ohlcv_data` 당일 행에도 UPSERT (수급 컬럼 보존)

## 3. ka10059 · 종목별 투자자 매매동향 (장 마감 +30분, 1회)

```text
POST {base}/api/dostk/stkinfo
headers: api-id: ka10059
body: { "dt": "YYYYMMDD", "stk_cd": "005930", "amt_qty_tp": "1", "trde_tp": "0", "unit_tp": "1" }
```

| 명세 항목 | REST 필드 | 적재 |
| --- | --- | --- |
| 일자 | `dt` | 매칭 키 |
| 개인투자자 순매수 | `ind_invsr` | (스냅샷 보관만) |
| 외국인투자자 순매수 | `frgnr_invsr` | `ohlcv_data.foreign_net` |
| 기관계 순매수 | `orgn` | `ohlcv_data.institution_net` |

- 장중 수급은 확정치가 아니므로 마감 후에만 반영한다.

## 4. 제약 사항 (REST 기준)

1. ~~Windows COM 전용~~ → **REST는 리눅스/Docker에서 직접 실행 가능** (구 명세 제약 1 폐기)
2. 레이트리밋: 호출 간 최소 간격 가드 적용 (`KIWOOM_MIN_REQUEST_INTERVAL_SEC`, 기본 0.25초)
3. 장 운영 시간: 평일 09:00~15:30 KST만 폴링. 공휴일은 미반영(주말 게이트만).
4. 수정주가: 기간(차트) 수집 시 적용 — 후속 백필 작업 범위.
5. **필드명 검증**: REST 응답 필드명은 키움 문서 기준 매핑이며, 모의 도메인 실호출
   (`uv run python -m app.main --once`)로 검증 후 상수만 조정하면 된다.

## 5. MVP 타깃 종목

타깃은 코드가 아니라 **DB `stocks.is_target = TRUE`** 로 관리한다 (Zero-Hardcoding).
원본 명세의 6종목(삼성전자·SK하이닉스·네이버·현대차·KB금융·POSCO홀딩스)은 seed로 등록.

## 6. 키움 API 미제공 데이터 (DART 보완)

원본 명세 §4와 동일 — 매출액/영업이익/이자비용/현금흐름 등은 DART `fnlttSinglAcntAll`로 수집.

---

*Signal α · Team LENS · Price Collector REST 데이터 명세*
