# Report RAG — 은진 다음 작업 목록

> 작성 기준: 2026-06-11  
> 기획서: `Signal_Alpha_기획서_v4.md` 기준  
> 담당: 은진 (Team LENS)

---

## 현재 상태 요약

| 파일 | 현재 방식 | v4 목표 |
|------|-----------|---------|
| `ReportCollector` | `parsed_reports.json` 직접 읽음 | `report_raw` DB 테이블에서 읽음 |
| `ReportAnalyzer` | `SourceResult` 반환만 함 | `report_signal` 테이블에도 저장 |
| `upside_pct` | 항상 `None` | `price_raw` 준비되면 쿼리로 계산 (규태 의존) |
| D-4 Aggregator | 미구현 | 선행성 가중 통합기 신규 구현 |
| `setup_db.py` | `report_chunks` 테이블만 생성 | `report_raw`, `report_signal` 테이블 추가 |

---

## 작업 1 — `report_raw` 테이블 생성 + Collector DB 연동

**우선순위: 높 / 예상 소요: 1~2시간**

### 해야 할 것

- [ ] `setup_db.py`에 `report_raw` 테이블 DDL 추가
- [ ] `parsers/vector_store.py`에서 청크 적재 시 `report_raw`에도 동시 저장
- [ ] `ReportCollector.collect()`를 JSON 대신 `report_raw` 쿼리로 전환
- [ ] JSON fallback 제거 (MVP 이후에는 DB가 single source of truth)

### `report_raw` 테이블 스키마 (초안)

```sql
CREATE TABLE IF NOT EXISTS report_raw (
    id           SERIAL PRIMARY KEY,
    stock_code   VARCHAR(10)  NOT NULL,
    firm         VARCHAR(50)  NOT NULL,
    date         VARCHAR(20)  NOT NULL,
    report_type  VARCHAR(30),
    title        TEXT,
    pdf_url      TEXT,
    target_price INT,
    opinion      VARCHAR(20),
    key_rationale TEXT,
    raw_text_preview TEXT,
    processed    BOOLEAN      DEFAULT false,
    created_at   TIMESTAMP    DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS report_raw_stock_idx ON report_raw (stock_code);
```

### 변경 대상 파일

| 파일 | 변경 내용 |
|------|-----------|
| `setup_db.py` | `report_raw` 테이블 + 인덱스 DDL 추가 |
| `parsers/vector_store.py` | 청크 저장 루프에서 `report_raw` INSERT 추가 |
| `app/collectors/report_collector.py` | `open(path)` → `psycopg2` 쿼리로 교체 |

---

## 작업 2 — `report_signal` 테이블 생성 + Analyzer 결과 저장

**우선순위: 높 / 예상 소요: 1시간**

### 해야 할 것

- [ ] `setup_db.py`에 `report_signal` 테이블 DDL 추가
- [ ] `ReportAnalyzer.analyze()` 끝에 결과를 `report_signal`에 INSERT/UPSERT
- [ ] 엔드포인트(`/agents/report`)는 현재 방식 그대로 유지 (반환값 변경 없음)

### `report_signal` 테이블 스키마 (초안)

```sql
CREATE TABLE IF NOT EXISTS report_signal (
    id               SERIAL PRIMARY KEY,
    stock_code       VARCHAR(10) NOT NULL,
    direction        VARCHAR(20),
    score            FLOAT,
    avg_target       FLOAT,
    upside_pct       FLOAT,
    target_trend     VARCHAR(20),
    conflict_detected BOOLEAN,
    opinions         JSONB,
    risk_flags       JSONB,
    summary          TEXT,
    data_status      VARCHAR(20),
    analyzed_at      TIMESTAMP   DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS report_signal_stock_idx ON report_signal (stock_code);
```

### 변경 대상 파일

| 파일 | 변경 내용 |
|------|-----------|
| `setup_db.py` | `report_signal` 테이블 + 인덱스 DDL 추가 |
| `app/analyzers/report_analyzer.py` | `SourceResult` 생성 후 DB INSERT 추가 |

---

## 작업 3 — `upside_pct` price_raw 연동 준비

**우선순위: 중 / 예상 소요: 30분 (로직만, 실제 동작은 규태 완료 후)**

### 배경

- `upside_pct = (avg_target - current_price) / current_price * 100`
- 현재 주가는 **규태(C-6)** 가 `price_raw` 테이블에 적재함 (키움증권 API)
- `Report_Analyzer`는 외부 API 직접 호출 금지 → `price_raw`에서만 읽어야 함

### 해야 할 것

- [ ] `report_analyzer.py`에 `_get_current_price(stock_code) -> float | None` 함수 추가
  - `price_raw` 테이블 없거나 데이터 없으면 `None` 반환 (graceful fallback)
- [ ] `upside_pct=None` 자리에 `_get_current_price()` 호출 결과 연결

### 구현 예시

```python
def _get_current_price(stock_code: str) -> float | None:
    try:
        import psycopg2
        from app.core.config import get_settings
        settings = get_settings()
        conn = psycopg2.connect(settings.database_url)
        cur = conn.cursor()
        cur.execute(
            "SELECT close FROM price_raw WHERE stock_code = %s ORDER BY date DESC LIMIT 1",
            (stock_code,)
        )
        row = cur.fetchone()
        conn.close()
        return float(row[0]) if row else None
    except Exception:
        return None
```

> `price_raw` 테이블 스키마는 규태와 맞춰야 함 (컬럼명 `close`, `date` 확인 필요)

---

## 작업 4 — D-4 선행성 가중 Aggregator 구현

**우선순위: 중 / 예상 소요: 2~3시간**

### 배경

기획서 6.1: 은진이 담당하는 Debate Aggregator 방식 D-4.  
각 소스의 `SourceResult`를 받아 선행성 가중치로 합산해 종합 점수 산출.

### 가중치 (기획서 6.2 기준)

| 소스 | 가중치 |
|------|--------|
| 채용 (`hiring`) | 0.30 |
| 특허 (`patent`) | 0.25 |
| 검색 (`datalab`) | 0.20 |
| 공시 (`dart`) | 0.15 |
| 리포트 (`report`) | 0.10 |
| 주가 (`price`) | 0.00 (검증용, 가중 제외) |

### 해야 할 것

- [ ] `app/aggregators/d4_leadingness_aggregator.py` 신규 파일 생성
- [ ] 입력: `dict[source, SourceResult]` (각 Analyzer 출력)
- [ ] 출력: `score`, `signal`, `source_agreement`, `summary`, `weights_used`
- [ ] 소스 없을 때 (`None`) graceful 처리: 해당 소스 제외하고 나머지 가중치 재정규화
- [ ] `/agents/analyze` 엔드포인트에 D-4 결과 필드 추가

### 파일 위치

```
services/agent-worker/app/
└── aggregators/
    ├── __init__.py
    └── d4_leadingness_aggregator.py
```

---

## 작업 순서 권장

```
[지금 당장]
1. 작업 1: report_raw 테이블 + Collector DB 연동
2. 작업 2: report_signal 테이블 + Analyzer 결과 저장
3. 작업 3: upside_pct 로직 미리 작성 (price_raw fallback 포함)

[다른 팀원 작업 진행되면]
4. 작업 3 실제 동작 확인 (규태 price_raw 완성 후)
5. 작업 4: D-4 Aggregator (광현/성진/이슬 SourceResult 연동 후)
```

---

## 팀 연계 포인트

| 팀원 | 은진이 기다리는 것 | 은진이 줘야 하는 것 |
|------|-------------------|-------------------|
| 규태 | `price_raw` 테이블 스키마 (`close`, `date` 컬럼명) | — |
| 광현 | — | `SourceResult` 구조 공유 (D-3 토론 입력용) |
| 성진·이슬 | 각자 `SourceResult` 완성 | D-4 Aggregator 입력 인터페이스 |
| 프론트 | — | `report_signal` 테이블 응답 구조 확정 |

---

## 서버 실행 방법 (변경 없음)

```powershell
docker start signal-pg
cd services/agent-worker
uvicorn app.main:app --reload --port 8001

# 테스트
curl -X POST http://localhost:8001/agents/report `
     -H "Content-Type: application/json" `
     -d '{"stock_code": "005930", "stock_name": "삼성전자"}'
```
