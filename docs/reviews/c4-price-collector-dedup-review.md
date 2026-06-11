# 코드 리뷰 — C-4 / C-4-1 price-collector 중복 데이터 처리

> 대상: `services/price-collector` (C-4 키움 주가·수급 수집기 #16, C-4-1 업종 시세·지수 수집기 #27)
> 범위: 키움 OpenAPI+로 같은 종목/업종의 데이터가 중복으로 들어올 때의 처리
> 결론 요약: **중복은 DB 유니크 키 + UPSERT로 구조적으로 차단**되어 재실행에 안전하다. 다만 **앱 메모리 병합 단계에 `trade_date` 중복 제거가 없어**, 향후 페이지네이션/재시도 concat 도입 시 데이터 정합성·실행 카운트가 흔들릴 수 있다(선제 하드닝 권장).

---

## 1. 대상 스키마

### `ohlcv_data` (`database/migrations/002_market.sql:12`)

```sql
CREATE TABLE IF NOT EXISTS ohlcv_data (
    id BIGSERIAL PRIMARY KEY,
    stock_id BIGINT NOT NULL REFERENCES stocks(id),
    trade_date DATE NOT NULL,
    open  NUMERIC(12,2) NOT NULL,
    high  NUMERIC(12,2) NOT NULL,
    low   NUMERIC(12,2) NOT NULL,
    close NUMERIC(12,2) NOT NULL,
    volume BIGINT NOT NULL,
    adjusted_close NUMERIC(12,2),
    foreign_net BIGINT,
    institution_net BIGINT,
    change_pct NUMERIC(6,2),
    market_cap BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_ohlcv UNIQUE (stock_id, trade_date)   -- 중복 방지의 핵심
);
CREATE INDEX idx_ohlcv_stock_date ON ohlcv_data (stock_id, trade_date DESC);
```

### 업종 관련 (`database/migrations/011_sector_market.sql`)

- `sectors` — `CONSTRAINT uq_sector UNIQUE (market, kiwoom_code)` → 업종 정의 중복 차단. 코스피/코스닥 종합지수는 `is_market_index = TRUE`인 `sectors` 행으로 통합 관리.
- `sector_ohlcv` — `CONSTRAINT uq_sector_ohlcv UNIQUE (sector_id, trade_date)` → 업종 시세 중복 차단.

---

## 2. 중복 처리는 2계층

### ✅ 계층 A — DB UPSERT (실질적 보증)

`app/storage/repository.py:35` (업종은 `app/storage/sector_repository.py`):

```sql
INSERT INTO ohlcv_data (...) VALUES (...)
ON CONFLICT (stock_id, trade_date) DO UPDATE SET
    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
    close = EXCLUDED.close, volume = EXCLUDED.volume,
    foreign_net     = COALESCE(EXCLUDED.foreign_net,     ohlcv_data.foreign_net),
    institution_net = COALESCE(EXCLUDED.institution_net, ohlcv_data.institution_net),
    change_pct      = COALESCE(EXCLUDED.change_pct,      ohlcv_data.change_pct),
    market_cap      = COALESCE(EXCLUDED.market_cap,      ohlcv_data.market_cap)
```

- **멱등성**: 같은 `(종목, 거래일)`을 다시 받아도 행이 늘지 않고 최신값으로 정정된다.
- **OHLCV는 덮어쓰기**: 수정주가(`수정주가구분=1`)·정정 반영.
- **수급/등락률/시총은 `COALESCE`로 보존**: 일봉만 재수집(수급이 NULL)해도 이전 값이 유실되지 않는다.
- **executemany 안전성**: `cur.executemany(_UPSERT_SQL, params)`(`repository.py:92`)는 N개의 개별 INSERT 문으로 실행되므로, 한 배치 안에 같은 키가 2번 있어도 `"ON CONFLICT ... cannot affect row a second time"` 에러 없이 **나중 값이 승리**한다. (단일 다중-VALUES INSERT였다면 에러)

### ⚠️ 계층 B — 앱 메모리 병합 (`trade_date` 중복 제거 없음)

`app/schemas/price.py:69` `build_ohlcv_rows`, `app/schemas/sector.py:62` `build_sector_ohlcv_rows` 모두 캔들을 정렬만 하고 **1:1로 행을 생성**한다.

```python
ordered = sorted(candles, key=lambda c: c.trade_date)   # dedup이 아니라 정렬
for candle in ordered:
    ...  # 같은 trade_date 캔들이 2개면 출력 행도 2개
```

- 현재는 `app/collectors/daily_chart.py:21`, `app/collectors/sector_daily_chart.py:24`가 **`next="0"` 단일 페이지만** 호출 → 한 페이지에서 같은 날짜가 중복될 일이 없어 **실제로는 문제가 발생하지 않는다.**
- 그러나 향후 **페이지네이션(`next=2` 연속 조회)이나 재시도 결과 concat**을 붙이면 페이지 경계에서 날짜가 겹쳐 중복 행이 생긴다. 이때:
  - DB는 UPSERT로 깨끗하지만,
  - `written_count = len(params)`(`repository.py:94`)라서 **`collector_runs`의 collected/inserted 카운트가 부풀려진다**(보고 수 ≠ 실제 distinct 저장 수).
  - `change_pct`가 중복 날짜끼리 계산되어 **0%로 왜곡**되고 `prev_close` 전이가 흐트러진다.

> 참고: 수급(flow)은 `flow_by_date = {flow.trade_date: flow ...}`(`price.py:80`) dict로 **이미 날짜 dedup**(마지막 값 채택)된다. 시총은 최신일 1행에만 부착된다.

---

## 3. 경로별 요약

### C-4 종목 (`app/pipeline.py:80`)
`resolve_stock_id`(미등록 ticker → `skipped`) → 일봉(OPT10081)·수급(OPT10059)·기본(OPT10001) 수집 → `build_ohlcv_rows`(일봉을 척추로 두고 수급은 날짜 join, 시총은 최신일) → `ohlcv_data` UPSERT. 한 종목 실패가 배치를 막지 않음(`pipeline.py:104`).

### C-4-1 업종 (`app/sector_pipeline.py`)
`list_active_sectors()` 순회(종합지수도 같은 루프) → OPT20006 일봉 → `build_sector_ohlcv_rows` → `sector_ohlcv` UPSERT. dedup 특성은 종목 경로와 동일.

> `app/collectors/sector_quote.py`(OPT20004 현재시세) 수집기는 존재하지만 파이프라인에 연결돼 있지 않아 적재 경로에 영향 없음.

---

## 4. 발견 사항 / 권장

| 심각도 | 항목 | 위치 | 권장 |
|---|---|---|---|
| ✅ 양호 | DB 유니크 + UPSERT 멱등, executemany 키충돌 내성 | `repository.py`, `002/011.sql` | 유지 |
| ⚠️ 중 | 병합 단계 `trade_date` 중복 미제거 (페이지네이션 도입 시 표면화) | `price.py:81`, `sector.py:67` | 행 생성 전 `{c.trade_date: c}`로 dedup(마지막/최신 채택) 후 정렬 |
| ⚠️ 하 | `written_count = len(params)`로 카운트 부풀림 가능 | `repository.py:94`, `sector_repository.py` | dedup된 행수 또는 `cur.rowcount` 사용 |
| ℹ️ 참고 | `market_cap`은 최초 수집 시 '최신일'에만 부착, 이후 `COALESCE`로 그 값 고정 | `price.py:91` | 의도면 OK, 주석/문서화 |
| ℹ️ 참고 | `settings.lookback_days`(기본 120)가 수집 경로에서 미사용(단일 TR 페이지) | `core/config.py:21`, `app/main.py` | 데이터 윈도우 정책 명시 또는 적용 |

### 권장 패치 스케치 (선제 하드닝)

```python
# build_ohlcv_rows / build_sector_ohlcv_rows 공통: trade_date 중복 제거(나중 값 채택)
by_date = {c.trade_date: c for c in candles}   # 같은 날짜는 마지막 캔들이 승리
ordered = sorted(by_date.values(), key=lambda c: c.trade_date)
```

```python
# upsert 반환값을 '시도 수'가 아니라 '실제 영향 행수'로
with self._conn.cursor() as cur:
    cur.executemany(_UPSERT_SQL, params)
    written = cur.rowcount   # 또는 len(params) 대신 dedup된 행수
```

---

## 5. 결론

- **지금 당장의 버그는 아니다.** 단일 페이지 호출 + DB 유니크/UPSERT 조합으로 중복은 안전하게 수렴한다.
- **유일한 실질 리스크는 향후 페이지네이션/재시도 concat 추가 시점**이다. 그 전에 병합 두 함수에 `trade_date` dedup 한 줄을 넣고 카운트를 보정하면 데이터 정합성(`change_pct`)과 실행 통계 정확성까지 방어된다.
- 후속 작업으로 위 ⚠️ 항목 2건을 한 커밋으로 처리하고 회귀 테스트(`tests/test_pipeline.py`, `tests/test_sector_pipeline.py`)에 중복-입력 케이스를 추가할 것을 권장한다.
