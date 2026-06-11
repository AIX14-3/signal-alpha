# Report RAG — 현재 상태 및 다음 구현 목록

> 작성 기준: 2026-06-09  
> 담당: 은진 (Team LENS)

---

## 1. 현재 완료된 것 (E6)

| 구분 | 파일 | 상태 |
|------|------|------|
| PDF 파싱 + LLM 추출 | `parsers/run_parser.py` | ✅ 완료 |
| JSON 품질 검증 (목표주가 7건 수정) | `data/parsed_reports.json` | ✅ 완료 |
| 텍스트 청킹 | `parsers/chunker.py` | ✅ 완료 |
| pgvector 적재 (457 chunks, 19개 리포트) | `parsers/vector_store.py` | ✅ 완료 |
| DB 스키마 (vector(1024), ivfflat index) | `setup_db.py` | ✅ 완료 |
| Report Collector (JSON → RawEvidence) | `app/collectors/report_collector.py` | ✅ 완료 |
| Report Analyzer (분석 + pgvector 검색) | `app/analyzers/report_analyzer.py` | ✅ 완료 |
| FastAPI 엔드포인트 | `app/api/routes/agents.py` | ✅ 완료 |
| SourceResult / ReportMeta 스키마 | `app/schemas/source_result.py` | ✅ 완료 |
| 증분 처리 (incremental flag) | `parsers/run_parser.py` | ✅ 완료 |

**수동 테스트 결과 (POST /agents/report)**

| 종목 | 리포트 수 | direction | avg_target | trend | conflict |
|------|-----------|-----------|------------|-------|----------|
| 삼성전자 (005930) | 10건 | positive | 89,600원 | up | ✅ (72k~115k) |
| SK하이닉스 (000660) | 4건 | positive | 377,500원 | up | ✅ |
| 네이버 (035420) | 5건 | positive | 295,000원 | up | ✅ |

---

## 2. 미완료 — 은진 담당

### 2-1. `upside_pct` 현재 주가 연동 (우선순위: 중)

**위치**: `app/analyzers/report_analyzer.py` → `ReportMeta.upside_pct`  
현재 항상 `None`으로 반환됨.

```python
# 현재
report_meta=ReportMeta(upside_pct=None, ...)

# 목표: 현재 주가 대비 목표주가 상승 여력 계산
# upside_pct = (avg_target - current_price) / current_price * 100
```

**구현 방법 (2가지 선택지)**

| 방법 | 장점 | 단점 |
|------|------|------|
| `pykrx` 라이브러리 | 무료, 한국거래소 공식 데이터 | 장 마감 후만 조회 가능 |
| 네이버 파이낸스 API (`finance.naver.com`) | 실시간 | 비공식 API, 언제든 막힐 수 있음 |

**추천**: `pykrx` 사용 (`pip install pykrx`)

```python
from pykrx import stock
current_price = stock.get_market_ohlcv_by_date("20260609", "20260609", stock_code)["종가"].iloc[-1]
```

---

### 2-2. PHASE 7 — 배치 스케줄링 (우선순위: 낮 — MVP 이후)

PDF를 자동으로 수집 → 파싱 → DB 적재하는 파이프라인 자동화.

**해야 할 것**

- [ ] 증권사별 리포트 자동 크롤러 (`parsers/crawlers/`)
  - 미래에셋 리서치 페이지 크롤링
  - 유진투자증권 리서치 페이지 크롤링
  - 신한투자증권 리서치 페이지 크롤링
- [ ] 스케줄러 설정: 매일 오전 7시 + 이벤트 노트 발생 시 6시간마다
- [ ] 90일 이상 된 청크 자동 아카이브 (DB에서 `archived` 플래그 처리)
- [ ] 스케줄러 실행 로그 저장

**예상 파일 구조**

```
parsers/
├── crawlers/
│   ├── mirae_crawler.py
│   ├── eugene_crawler.py
│   └── shinhan_crawler.py
└── scheduler.py   # APScheduler or cron
```

---

### 2-3. `raw_evidence` DB 테이블 연동 (우선순위: 낮 — 아키텍처 개선)

현재 `ReportCollector`는 `parsed_reports.json` 파일을 직접 읽음.  
최종 아키텍처에서는 DB의 `raw_evidence` 테이블에서 읽어야 함.

```
현재: parsed_reports.json → ReportCollector → RawEvidence
목표: raw_evidence (DB) → ReportCollector → RawEvidence
```

**해야 할 것**

- [ ] `raw_evidence` 테이블 스키마 정의 (`setup_db.py` 또는 마이그레이션)
- [ ] `parsers/vector_store.py`에서 DB 적재 시 `raw_evidence` 테이블에도 동시 저장
- [ ] `ReportCollector.collect()` 에서 JSON 대신 DB 쿼리로 전환

> 💡 MVP 단계에서는 JSON 방식으로도 동작하므로, 배치 스케줄링 구현 후에 같이 진행하면 효율적.

---

## 3. 다른 팀원 담당 — 연계 포인트

### 3-1. E3 Main Server API (백엔드 팀 담당)

은진이 직접 구현하지 않지만, **Report Analyzer가 반환하는 `SourceResult` 구조를 Main Server가 그대로 소비**함.  
아래 엔드포인트가 구현되어야 대시보드와 연결됨.

| 엔드포인트 | 역할 |
|-----------|------|
| `GET /api/watchlist` | 관심 종목 목록 |
| `POST /api/watchlist` | 종목 추가 |
| `POST /api/signals/run/{stockCode}` | agent-worker 호출 트리거 |
| `GET /api/signals/latest` | 가장 최근 분석 결과 조회 |

**은진이 확인해야 할 것**: Main Server가 `POST /agents/report`를 제대로 호출하는지, 응답 JSON 구조가 맞는지 같이 리뷰.

---

### 3-2. E8 Debate Aggregation (광현 담당)

세 에이전트(DART Watcher, Report RAG, Alternative Signal)가 모두 `SourceResult`를 반환하면  
광현이 이걸 받아서 `AggregatedSignal`을 만드는 Debate Aggregation을 구현함.

**현재 Report RAG가 반환하는 SourceResult 구조 (광현에게 공유)**

```python
@dataclass(frozen=True)
class SourceResult:
    source: Literal["report", "dart", "alternative"]
    stock_code: str
    direction: Literal["positive", "neutral", "negative", "mixed", "unknown"]
    score: float          # 0~100, 50이 중립
    summary: str
    evidence_items: list[EvidenceItem]
    risk_flags: list[str]
    data_status: Literal["ok", "partial", "failed"]
    report_meta: ReportMeta | None   # Report RAG 전용

@dataclass(frozen=True)
class ReportMeta:
    avg_target: float | None
    upside_pct: float | None         # 현재 주가 미연동 — None
    target_trend: Literal["up", "down", "flat", "unknown"]
    conflict_detected: bool
    opinions: list[dict]             # [{"firm": "미래에셋", "target": 90000, "view": "buy"}, ...]
```

**E8 구현 시작 조건**: DART Watcher(성진)와 Alternative Signal(이슬)도 `SourceResult`를 반환할 수 있어야 함.

---

### 3-3. E9 Web Dashboard (프론트 담당)

- `consensus_score` 표시 (용어 주의: `confidence` 아님)
- `alignment_rate` 표시
- 리스크 플래그 카드 (`risk_flags` 배열)
- `ReportMeta.target_trend` 화살표 표시 (↑/↓/→)

---

## 4. 작업 순서 권장

```
[지금 당장]
1. upside_pct 연동 (pykrx) — 작업량 소 (~1시간)

[MVP 테스트 후]
2. 다른 팀원 SourceResult 구조 공유 + E8 연계 리뷰
3. Main Server /api/signals/run 연동 테스트

[MVP 이후 개선]
4. raw_evidence DB 테이블 연동
5. PHASE 7 배치 스케줄링 + 크롤러
```

---

## 5. 현재 서버 실행 방법 (참고)

```powershell
# pgvector 컨테이너 실행
docker start signal-pg

# agent-worker 서버 실행 (port 8001)
cd services/agent-worker
uvicorn app.main:app --reload --port 8001

# 테스트
curl -X POST http://localhost:8001/agents/report `
     -H "Content-Type: application/json" `
     -d '{"stock_code": "005930", "stock_name": "삼성전자"}'
```
