# PR 작업 리포트 — feature_eunjin

> **담당자**: 서은진 | **브랜치**: feature_eunjin → main
> **기간**: 2026-06-08 ~ 2026-06-11

---

## 작업 요약

Report RAG 파이프라인 전체 구현 완료.
네이버 금융 크롤러부터 PDF 파싱, 벡터 DB 적재, 분석기, REST API까지 end-to-end 파이프라인을 구축했습니다.
마지막으로 파일 구조를 팀 표준(app 모듈 분리)으로 재배치하고 PDF·로그 파일 gitignore 처리를 완료했습니다.

---

## 구현 내용

### 1. 크롤러 (`collectors/report/crawler.py`)

- 네이버 금융 리서치 페이지에서 증권사 리포트 목록 수집
- 첨부 컬럼(`cols[3]`)에서 `pdf_direct_url` 직접 추출하도록 개선
- 미래에셋, 유진투자, 신한투자증권 대상 (삼성전자 / SK하이닉스 / 네이버)
- 결과 → `data/report_list.json` 저장

### 2. PDF 자동 다운로더 (`collectors/report/pdf_downloader.py`) — 신규

- `report_list.json`의 `pdf_direct_url`을 읽어 `data/reports/` 하위에 자동 다운로드
- `--incremental` 플래그: 이미 존재하는 파일 스킵
- **미래에셋 / 유진투자** → 자동 다운로드 가능
- **신한투자증권** → Naver 미제공으로 수동 보완 필요 (5건)

### 3. PDF 파서 (`collectors/report/parsers/`)

| 파일 | 역할 |
|------|------|
| `pdf_extractor.py` | PyMuPDF로 PDF 텍스트 추출 |
| `llm_parser.py` | GPT-4o-mini로 목표주가·투자의견·핵심근거 추출 |
| `run_parser.py` | 전체 PDF 일괄 파싱 → `parsed_reports.json` 저장 |
| `chunker.py` | 500토큰 단위 청킹 (RecursiveCharacterTextSplitter) |
| `vector_store.py` | BGE-M3 임베딩 후 pgvector 적재, `--backfill-raw` 모드 |

- 14건 파싱 완료 / 7건 수동 교정 완료 (`data/parsed_reports.json`)
- 신한 5건 미수집으로 총 19건 중 14건 처리

### 4. DB 연동 (`setup_db.py`, `collectors/report/collector.py`)

- `report_raw` 테이블: 파싱된 리포트 메타 + 핵심 근거 저장
- `report_signal` 테이블: 분석 결과(upside_pct, confidence, signal) 저장
- `collector.py`: `report_list.json` fallback → `report_raw` DB 쿼리 전환

### 5. 분석기 (`analyzers/report/analyzer.py`)

- `upside_pct` 계산: `price_raw` 테이블의 현재가 연동
- 분석 결과를 `report_signal` 테이블에 저장
- `SourceResult`, `ReportMeta`, `EvidenceItem` 스키마 추가

### 6. API (`api/routes/report.py`)

| 엔드포인트 | 설명 |
|-----------|------|
| `POST /agents/report` | 종목코드 입력 → 리포트 수집·파싱 실행 |
| `POST /agents/analyze` | 종목코드 입력 → 분석 결과 반환 |

### 7. 파일 구조 재배치 (팀 표준 적용)

| 이전 경로 | 이후 경로 |
|----------|----------|
| `analyzers/report_analyzer.py` | `analyzers/report/analyzer.py` |
| `collectors/report_collector.py` | `collectors/report/collector.py` |
| `api/routes/agents.py` | `api/routes/report.py` |
| `parsers/` (루트) | `collectors/report/parsers/` |
| `crawlers/naver_report_crawler.py` | `collectors/report/crawler.py` |
| `setup_db.py` (루트) | `services/agent-worker/setup_db.py` |
| `test_rag.py` (루트) | `services/agent-worker/tests/test_rag.py` |

### 8. gitignore 정리

- `data/reports/` (PDF 파일) gitignore 추가
- `*.log`, `*.pdf` gitignore 추가
- 기존 추적 중이던 개인 문서 파일 git 추적 제거

---

## 현재 파이프라인 상태

```
크롤러 (crawler.py)          ✅ 완료 — pdf_direct_url 직접 추출
      ↓
PDF 자동 다운로드 (pdf_downloader.py)
                             ✅ 미래에셋·유진 자동 | 신한 수동 필요
      ↓
PDF 텍스트 추출 (pdf_extractor.py)
                             ✅ 완료 — 14건
      ↓
LLM 파싱 (llm_parser.py)     ✅ 완료 — 14건 (7건 수동 교정)
      ↓
청킹·임베딩 (chunker.py + vector_store.py)
                             ✅ 코드 완료 — DB 적재는 PostgreSQL 설치 후 실행
      ↓
report_raw DB 저장           🔲 PostgreSQL 설치 후 진행
      ↓
분석기 (analyzer.py)         ✅ 코드 완료 — DB 연동 후 실행
      ↓
report_signal DB 저장        🔲 DB 설치 후 진행
      ↓
API (/agents/report, /agents/analyze)
                             ✅ 엔드포인트 구현 완료
```

---

## 팀원 연계 포인트

- **DB 담당자**: `setup_db.py` 실행으로 `report_raw`, `report_signal` 테이블 생성 필요
  ```bash
  cd services/agent-worker
  python setup_db.py
  ```
- **분석기 팀**: `SourceResult` 스키마(`schemas/source_result.py`)가 추가됨 — report 분석 결과 공유 인터페이스
- **신한투자증권 PDF 5건**: 수동으로 `data/reports/` 하위에 추가 후 `run_parser.py` 재실행 필요

---

## 남은 작업

| 항목 | 담당 | 비고 |
|------|------|------|
| PostgreSQL + pgvector 설치 | 은진 | Docker 권장 |
| `python vector_store.py` 실행 (265청크 적재) | 은진 | DB 설치 후 |
| RAG 검색 테스트 (`test_rag.py`) | 은진 | 유사도 0.7+ 확인 |
| 신한 PDF 5건 수동 보완 | 은진 | Naver 링크에서 직접 다운로드 |
| 배치 스케줄링 (PHASE 7) | 은진 | 미시작 |

---

## 커밋 목록

| 해시 | 내용 |
|------|------|
| `8fa98b3` | feat: PDF 자동 다운로더 추가 및 크롤러 직접 URL 수집 개선 |
| `353e73f` | chore: PDF·로그·개인문서 gitignore 처리 및 git 추적 제거 |
| `12796b1` | refactor: 파일 구조를 팀 표준(app 모듈 분리) 구조로 재배치 |
| `9fa7a1a` | feat: Report RAG 파이프라인 완성 — report_raw/signal DB 연동 및 분석기 개선 |
| `1c447a0` | feat: 은진 수정사항 반영 (2026-06-09) |
| `f1266cf` | feat: 은진 수정사항 반영 (2026-06-08) |
