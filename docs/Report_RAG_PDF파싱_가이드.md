# Report RAG — PDF 파싱 & 벡터 DB 적재 가이드

> **팀 LENS | Signal α**
> PHASE 5~6: PDF 텍스트 추출 → LLM 파싱 → 벡터 DB 적재

---

## 현재 상태 (2026-06-09 기준)

| 항목 | 상태 |
|---|---|
| 크롤링 목록 | ✅ `data/report_list.json` — 19건 |
| PDF 수동 저장 | ✅ 14개 저장 완료 (신한 5건 미수집) |
| LLM 파싱 | ✅ 14건 파싱 완료 → `data/parsed_reports.json` |
| 파싱 품질 검증 | ✅ 이상값 수정 완료 (7건 목표주가·투자의견 수동 교정) |
| 청킹·임베딩 코드 | ✅ `parsers/chunker.py`, `parsers/vector_store.py` 작성 완료 |
| PostgreSQL 설치 | 🔲 미설치 → DB 설치 후 적재 진행 가능 |
| 벡터 DB 적재 | 🔲 미시작 (예상 총 265청크) |

### 저장된 PDF 현황

| 종목 | 증권사 | 파일명 | 유형 |
|---|---|---|---|
| 삼성전자 | 유진투자증권 | `eugene_20250709_cr.pdf` | company_report |
| 삼성전자 | 미래에셋증권 | `mirae_20250714_er.pdf` | earnings_review |
| 삼성전자 | 미래에셋증권 | `mirae_20250729_cr.pdf` | company_report |
| 삼성전자 | 유진투자증권 | `eugene_20250729_cr.pdf` | company_report |
| 삼성전자 | 미래에셋증권 | `mirae_20250801_cr.pdf` | company_report |
| 삼성전자 | 유진투자증권 | `eugene_20250801_cr.pdf` | company_report |
| 삼성전자 | 미래에셋증권 | `mirae_20250915_cr.pdf` | company_report |
| 삼성전자 | 미래에셋증권 | `mirae_20250922_cr.pdf` | company_report |
| SK하이닉스 | 미래에셋증권 | `mirae_20250714_cr.pdf` | company_report |
| SK하이닉스 | 유진투자증권 | `eugene_20250725_cr.pdf` | company_report |
| 네이버 | 미래에셋증권 | `mirae_20250808_er.pdf` | earnings_review |
| 네이버 | 미래에셋증권 | `mirae_20250814_cr.pdf` | company_report |
| 네이버 | 유진투자증권 | `eugene_20250811_cr.pdf` | company_report |
| 네이버 | 미래에셋증권 | `mirae_20250918_cr.pdf` | company_report |

### 신한투자증권 미수집 PDF (추후 보완)

| 종목 | 제목 | 날짜 | 링크 |
|---|---|---|---|
| 삼성전자 | 메모리 빅사이클에 예외는 없다 | 25.09.30 | [링크](https://finance.naver.com/research/company_read.naver?nid=86562) |
| 삼성전자 | 경쟁력 회복 기대감 재점화 | 25.08.01 | [링크](https://finance.naver.com/research/company_read.naver?nid=85146) |
| SK하이닉스 | 실적 기대감 ON, 우려는 잠시 OFF | 25.09.30 | [링크](https://finance.naver.com/research/company_read.naver?nid=86561) |
| SK하이닉스 | 해소될 우려와 지속될 경쟁우위 | 25.07.25 | [링크](https://finance.naver.com/research/company_read.naver?nid=84686) |
| 네이버 | 아직은 흐릿한 AI 수익화 그림 | 25.08.11 | [링크](https://finance.naver.com/research/company_read.naver?nid=85596) |

---

## 전체 흐름

```
저장된 PDF 파일
      ↓
STEP 1. PDF 텍스트 추출 (PyMuPDF)              ✅ 완료
      ↓
STEP 2. LLM 파싱 — 3가지 추출                  ✅ 완료
        목표주가 / 투자의견 / 핵심 근거
      ↓
STEP 3. 결과 JSON 저장 (parsed_reports.json)   ✅ 완료
      ↓
STEP 3.5. 파싱 품질 검증                       ✅ 완료 (7건 수동 교정)
      ↓
STEP 4. 청킹 + 임베딩 코드 준비                ✅ chunker.py 완료
        500토큰 단위 분할 / BGE-M3 임베딩       (예상 265청크 / 14건)
      ↓
STEP 4.5. PostgreSQL + pgvector 설치           🔲 미완료 ← 지금 여기
      ↓
STEP 5. pgvector 적재                          🔲 미시작
        메타데이터 + 벡터 저장
      ↓
STEP 6. RAG 검색 테스트                        🔲 미시작
        "SK하이닉스 최신 목표주가" 쿼리 테스트
```

---

## STEP 1 — PDF 텍스트 추출

### 라이브러리 설치

```bash
pip install pymupdf openai python-dotenv
```

### 코드 (`parsers/pdf_extractor.py`)

```python
import fitz
from pathlib import Path


def extract_text(pdf_path: str | Path) -> str:
    """PDF에서 전체 텍스트 추출"""
    doc = fitz.open(str(pdf_path))
    pages = [page.get_text("text") for page in doc]
    doc.close()
    return "\n".join(pages)


def extract_first_pages(pdf_path: str | Path, n: int = 3) -> str:
    """앞 n페이지만 추출 (목표주가·투자의견은 대부분 1~3페이지)"""
    doc = fitz.open(str(pdf_path))
    pages = []
    for i, page in enumerate(doc):
        if i >= n:
            break
        pages.append(page.get_text("text"))
    doc.close()
    return "\n".join(pages)
```

### 추출 품질 테스트 (먼저 실행)

```python
# parsers/ 디렉토리에서 실행
from pdf_extractor import extract_first_pages

text = extract_first_pages("../data/reports/samsung/mirae_20250714_er.pdf", n=3)
print(text[:1000])
```

**확인 포인트**

| 체크 | 내용 |
|---|---|
| 한글 정상 출력 | "삼성전자", "목표주가" 등이 깨지지 않고 나옴 |
| 목표주가 숫자 | "98,000" 또는 "98000" 형태로 보임 |
| 투자의견 | "매수", "BUY" 등이 보임 |
| 표 구조 | 표가 텍스트로 추출될 때 칸이 섞이는 경우 있음 (무방) |

---

## STEP 2 — LLM 파싱 (3가지 추출)

### .env 설정

```
OPENAI_API_KEY=sk-...
```

`.env` 파일은 프로젝트 루트(`signal-alpha/`)에 위치해야 합니다.

### 코드 (`parsers/llm_parser.py`)

```python
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from openai import OpenAI

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

PARSE_PROMPT = """
당신은 증권사 리포트 분석 전문가입니다.
아래 리포트 텍스트에서 다음 3가지를 추출하세요.

추출 항목:
1. target_price: 목표주가 (숫자만, 없으면 null)
2. opinion: 투자의견 (buy / neutral / sell / unknown 중 하나)
   - 매수, BUY, 강력매수 → "buy"
   - 중립, HOLD, 보유 → "neutral"
   - 매도, SELL → "sell"
   - 불명확 → "unknown"
3. key_rationale: 핵심 투자 근거 (2~4문장 요약, 한국어)
   - 왜 그 투자의견과 목표주가를 제시하는지
   - 실적·업황·밸류에이션 근거 중심
   - 단순 수치 나열 금지, 판단 근거 중심

반드시 아래 JSON 형식으로만 답하세요. 다른 텍스트 없이 JSON만 출력하세요.

{
  "target_price": 숫자 또는 null,
  "opinion": "buy" | "neutral" | "sell" | "unknown",
  "key_rationale": "핵심 근거 2~4문장"
}
"""


def get_client() -> OpenAI:
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key or api_key.startswith("sk-proj-your-key"):
        raise ValueError(
            "OPENAI_API_KEY가 설정되지 않았습니다. "
            "프로젝트 루트의 .env 파일에 실제 API 키를 입력하세요."
        )
    return OpenAI(api_key=api_key)


def parse_report(text: str, model: str = "gpt-4o-mini") -> dict:
    """리포트 텍스트에서 목표주가·투자의견·핵심 근거 추출"""
    truncated = text[:3000]
    client = get_client()

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": PARSE_PROMPT},
                {"role": "user", "content": f"리포트 텍스트:\n\n{truncated}"},
            ],
            temperature=0,
            response_format={"type": "json_object"},
        )
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"  [파싱 오류] {e}")
        return {
            "target_price": None,
            "opinion": "unknown",
            "key_rationale": "",
        }
```

---

## STEP 3 — 전체 파싱 실행 및 결과 저장

### 코드 (`parsers/run_parser.py`)

```python
"""
PDF 리포트 일괄 파싱
- PDF 텍스트 추출 → LLM 파싱 → parsed_reports.json 저장
"""
import argparse
import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

from pdf_extractor import extract_first_pages
from llm_parser import parse_report

ROOT_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT_DIR / "data"
REPORTS_DIR = DATA_DIR / "reports"
LIST_PATH = DATA_DIR / "report_list.json"
OUTPUT_PATH = DATA_DIR / "parsed_reports.json"

FIRM_CODE_MAP = {
    "shinhan": "신한투자증권",
    "mirae": "미래에셋증권",
    "eugene": "유진투자증권",
}

FOLDER_CODE_MAP = {
    "samsung": "005930",
    "skhynix": "000660",
    "naver": "035420",
}

TYPE_MAP = {
    "er": "earnings_review",
    "en": "event_note",
    "cr": "company_report",
    "ep": "earnings_preview",
}


def parse_filename(pdf_path: Path) -> dict:
    """파일명에서 증권사, 날짜, 유형 파싱"""
    parts = pdf_path.stem.split("_")
    firm_code = parts[0]
    date_str = parts[1]
    type_code = parts[2] if len(parts) > 2 else "cr"

    return {
        "firm": FIRM_CODE_MAP.get(firm_code, firm_code),
        "date_short": f"{date_str[2:4]}.{date_str[4:6]}.{date_str[6:]}",
        "date_long": f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:]}",
        "report_type": TYPE_MAP.get(type_code, "company_report"),
        "stock_code": FOLDER_CODE_MAP.get(pdf_path.parent.name, ""),
    }


def find_matched_report(report_list: list[dict], meta: dict) -> dict | None:
    """report_list.json에서 매칭 항목 찾기"""
    for report in report_list:
        if report["firm"] != meta["firm"]:
            continue
        if report["stock_code"] != meta["stock_code"]:
            continue
        if report["date"] in (meta["date_short"], meta["date_long"]):
            return report
    return None


def run(extract_only: bool = False) -> list[dict]:
    with open(LIST_PATH, encoding="utf-8") as f:
        report_list = json.load(f)

    results = []
    pdf_files = sorted(REPORTS_DIR.glob("*/*.pdf"))
    print(f"PDF 파일 {len(pdf_files)}개 파싱 시작\n")

    for pdf_path in pdf_files:
        print(f"[파싱] {pdf_path.parent.name}/{pdf_path.name}")

        meta = parse_filename(pdf_path)
        text = extract_first_pages(pdf_path, n=3)

        if not text.strip():
            print("  [경고] 텍스트 추출 실패 → 스킵")
            continue

        if extract_only:
            parsed = {"target_price": None, "opinion": "unknown", "key_rationale": ""}
            print("  [extract-only] LLM 파싱 스킵")
        else:
            parsed = parse_report(text)
            print(f"  목표주가: {parsed.get('target_price')}")
            print(f"  투자의견: {parsed.get('opinion')}")
            rationale = parsed.get("key_rationale", "")
            print(f"  핵심근거: {rationale[:80]}{'...' if len(rationale) > 80 else ''}")

        matched = find_matched_report(report_list, meta)

        results.append({
            "pdf_file": str(pdf_path.relative_to(ROOT_DIR)).replace("\\", "/"),
            "firm": meta["firm"],
            "stock_code": meta["stock_code"],
            "date": meta["date_long"],
            "report_type": meta["report_type"],
            "title": matched["title"] if matched else "",
            "pdf_url": matched["pdf_url"] if matched else "",
            "target_price": parsed.get("target_price"),
            "opinion": parsed.get("opinion", "unknown"),
            "key_rationale": parsed.get("key_rationale", ""),
            "raw_text_preview": text[:500],
            "processed": not extract_only,
        })

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n완료: {len(results)}건 파싱")
    print(f"저장: {OUTPUT_PATH}")
    return results


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PDF 리포트 파싱")
    parser.add_argument(
        "--extract-only",
        action="store_true",
        help="텍스트 추출만 수행 (OPENAI_API_KEY 없을 때 테스트용)",
    )
    args = parser.parse_args()
    run(extract_only=args.extract_only)
```

### 실행 방법

> **중요**: `run_parser.py`는 `parsers/` 디렉토리 내에서 실행해야 합니다.
> (내부 import가 `from pdf_extractor import ...` 로 같은 폴더를 기준으로 함)

```bash
# parsers/ 디렉토리로 이동 후 실행
cd parsers

# 텍스트 추출만 테스트 (API 키 불필요)
python run_parser.py --extract-only

# LLM 파싱 포함 전체 실행
python run_parser.py
```

### 기대 출력

```
PDF 파일 14개 파싱 시작

[파싱] samsung/mirae_20250714_er.pdf
  목표주가: 78000
  투자의견: buy
  핵심근거: 삼성전자는 여전히 스마트폰과 플렉서블 디스플레이, 범용 메모리 시장에서의 시장 지배력을...

...

완료: 14건 파싱
저장: data/parsed_reports.json
```

---

## STEP 3.5 — 파싱 품질 검증 ⚠️

파싱이 완료된 후 `parsed_reports.json`을 육안으로 검증해야 합니다.

### 현재 파싱 결과 요약 (2026-06-09 기준)

| 파일 | 목표주가 | 투자의견 | 이슈 |
|---|---|---|---|
| `eugene_20250811_cr.pdf` (NAVER 유진) | 280,000 | buy | 정상 |
| `mirae_20250808_er.pdf` (NAVER 미래 ER) | null | neutral | ⚠️ 목표주가 미추출 (ER이라 없을 수도) |
| `mirae_20250814_cr.pdf` (NAVER 미래) | null | unknown | ⚠️ 목표주가·의견 미추출 확인 필요 |
| `mirae_20250918_cr.pdf` (NAVER 미래) | null | unknown | ⚠️ 미추출 |
| `eugene_20250709_cr.pdf` (삼성 유진) | 72,000 | buy | 정상 |
| `eugene_20250729_cr.pdf` (삼성 유진) | 84,000 | buy | 정상 |
| `eugene_20250801_cr.pdf` (삼성 유진) | 84,000 | buy | 정상 |
| `mirae_20250714_er.pdf` (삼성 미래 ER) | 78,000 | buy | 정상 |
| `mirae_20250729_cr.pdf` (삼성 미래) | null | buy | ⚠️ 목표주가 미추출 (이벤트 노트성 리포트) |
| `mirae_20250801_cr.pdf` (삼성 미래) | **100** | buy | 🚨 이상값! SOTP 표에서 잘못 추출된 것으로 추정 |
| `mirae_20250915_cr.pdf` (삼성 미래) | null | buy | ⚠️ 미추출 |
| `mirae_20250922_cr.pdf` (삼성 미래) | null | buy | ⚠️ 미추출 |
| `eugene_20250725_cr.pdf` (SKH 유진) | 330,000 | buy | 정상 |
| `mirae_20250714_cr.pdf` (SKH 미래) | null | unknown | ⚠️ 미추출 |

### 이상값 처리 방법

**`target_price: 100` (mirae_20250801_cr.pdf)**
- SOTP 밸류에이션 표의 숫자 100을 목표주가로 혼동한 것으로 추정
- 실제 목표주가는 `raw_text_preview`에서 수동 확인 후 직접 수정하거나 재파싱
- 재파싱 시 n=5 페이지로 늘려서 시도 권장

```python
# 단일 파일 재파싱 예시 (parsers/ 에서 실행)
from pdf_extractor import extract_first_pages
from llm_parser import parse_report
import json

text = extract_first_pages("../data/reports/samsung/mirae_20250801_cr.pdf", n=5)
result = parse_report(text)
print(json.dumps(result, ensure_ascii=False, indent=2))
```

**`target_price: null` 케이스 처리**
- 이벤트성 리포트(공시 직후)는 목표주가를 안 쓰는 경우 있음 → null 유지
- 정기 company_report인데 null이면 → n=5 페이지로 재파싱 시도
- 그래도 null이면 → 해당 PDF를 직접 열어서 목표주가 확인 후 수동 입력

---

## STEP 4 — 청킹 + 임베딩

### 개념

| 항목 | 결정값 | 이유 |
|---|---|---|
| 청크 크기 | 500토큰 | project-context.md 명세 |
| 청크 오버랩 | 50토큰 | 문맥 경계 손실 방지 |
| 임베딩 모델 | BGE-M3 | 한국어 특화, 무료 |
| 임베딩 대안 | text-embedding-3-small | OpenAI, 빠름·유료 |

### 라이브러리 설치

```bash
# BGE-M3 사용 시
pip install sentence-transformers langchain-text-splitters

# OpenAI 임베딩 사용 시 (더 간단)
pip install openai tiktoken langchain-text-splitters
```

### 코드 (`parsers/chunker.py`)

```python
from langchain_text_splitters import RecursiveCharacterTextSplitter


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """텍스트를 청크 단위로 분할"""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", ".", " ", ""],
    )
    return splitter.split_text(text)
```

### 청킹 테스트

```python
# parsers/ 에서 실행
from pdf_extractor import extract_text
from chunker import chunk_text

text = extract_text("../data/reports/samsung/mirae_20250714_er.pdf")
chunks = chunk_text(text)
print(f"총 {len(chunks)}개 청크")
print(f"\n--- 청크 1 ---\n{chunks[0]}")
print(f"\n--- 청크 2 ---\n{chunks[1]}")
```

---

## STEP 4.5 — PostgreSQL + pgvector 설치 (Windows)

> PostgreSQL이 설치되어 있지 않다면 아래 중 하나로 설치합니다.

### 옵션 A — Docker (권장, 가장 빠름)

```powershell
# Docker Desktop이 설치되어 있어야 함
docker run -d \
  --name signal-pg \
  -e POSTGRES_PASSWORD=password \
  -e POSTGRES_DB=signal_alpha \
  -p 5432:5432 \
  pgvector/pgvector:pg16

# 실행 확인
docker ps
```

`.env` 파일에 추가:
```
DATABASE_URL=postgresql://postgres:password@localhost:5432/signal_alpha
```

### 옵션 B — 직접 설치 (PostgreSQL 16 + pgvector)

1. [PostgreSQL 16 Windows 설치 파일](https://www.enterprisedb.com/downloads/postgres-postgresql-downloads) 다운로드 후 설치
2. 설치 시 비밀번호 설정 (예: `password`)
3. 설치 후 pgvector 설치:

```powershell
# PostgreSQL bin 경로를 PATH에 추가한 후
cd "C:\Program Files\PostgreSQL\16\bin"

# pgvector 확장 설치 (Windows용 바이너리)
# https://github.com/pgvector/pgvector/releases 에서 Windows 빌드 다운로드
# 또는 pip 방식 사용:
pip install pgvector
```

### DB 및 테이블 생성

```sql
-- psql 또는 DBeaver, pgAdmin에서 실행
-- Docker라면: docker exec -it signal-pg psql -U postgres

CREATE DATABASE signal_alpha;
\c signal_alpha

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE report_chunks (
    id           SERIAL PRIMARY KEY,
    stock_code   VARCHAR(10)  NOT NULL,
    firm         VARCHAR(50)  NOT NULL,
    date         VARCHAR(20)  NOT NULL,
    report_type  VARCHAR(30)  NOT NULL,
    title        TEXT,
    pdf_url      TEXT,
    target_price INT,
    opinion      VARCHAR(20),
    key_rationale TEXT,
    chunk_text   TEXT         NOT NULL,
    embedding    vector(1024),
    created_at   TIMESTAMP    DEFAULT NOW()
);

CREATE INDEX ON report_chunks USING ivfflat (embedding vector_cosine_ops);
```

### 연결 테스트

```python
# 프로젝트 루트에서
python -c "
import os
from dotenv import load_dotenv
load_dotenv('.env')
import psycopg2
conn = psycopg2.connect(os.environ['DATABASE_URL'])
cur = conn.cursor()
cur.execute(\"SELECT extname FROM pg_extension WHERE extname = 'vector'\")
print('pgvector:', cur.fetchone())
conn.close()
"
```

---

## STEP 5 — pgvector 적재

### 전제

Signal α는 `pgvector`를 벡터 DB로 사용합니다 (project-context.md 기준).

### 필요 패키지

```bash
pip install psycopg2-binary pgvector sqlalchemy
```

### DB 스키마

```sql
-- PostgreSQL에서 실행
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE report_chunks (
    id          SERIAL PRIMARY KEY,
    stock_code  VARCHAR(10)  NOT NULL,
    firm        VARCHAR(50)  NOT NULL,
    date        VARCHAR(20)  NOT NULL,
    report_type VARCHAR(30)  NOT NULL,
    title       TEXT,
    pdf_url     TEXT,
    target_price INT,
    opinion     VARCHAR(20),
    key_rationale TEXT,
    chunk_text  TEXT         NOT NULL,
    embedding   vector(1024),        -- BGE-M3: 768차원
    created_at  TIMESTAMP    DEFAULT NOW()
);

CREATE INDEX ON report_chunks USING ivfflat (embedding vector_cosine_ops);
```

### 코드 (`parsers/vector_store.py`)

```python
import json
import os
import psycopg2
from pathlib import Path
from sentence_transformers import SentenceTransformer
from chunker import chunk_text
from pdf_extractor import extract_text

model = SentenceTransformer("BAAI/bge-m3")
ROOT_DIR = Path(__file__).resolve().parent.parent
DB_URL = os.getenv("DATABASE_URL", "postgresql://localhost/signal_alpha")


def embed_and_store(parsed_path: str = None):
    if parsed_path is None:
        parsed_path = str(ROOT_DIR / "data" / "parsed_reports.json")

    with open(parsed_path, encoding="utf-8") as f:
        reports = json.load(f)

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    total_chunks = 0

    for report in reports:
        pdf_file = ROOT_DIR / report["pdf_file"]
        full_text = extract_text(pdf_file)
        chunks = chunk_text(full_text)
        print(f"[{report['firm']}] {report['date']} → {len(chunks)}청크")

        embeddings = model.encode(chunks, normalize_embeddings=True)

        for chunk, emb in zip(chunks, embeddings):
            cur.execute("""
                INSERT INTO report_chunks
                    (stock_code, firm, date, report_type, title, pdf_url,
                     target_price, opinion, key_rationale, chunk_text, embedding)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            """, (
                report["stock_code"],
                report["firm"],
                report["date"],
                report["report_type"],
                report.get("title", ""),
                report.get("pdf_url", ""),
                report.get("target_price"),
                report.get("opinion", "unknown"),
                report.get("key_rationale", ""),
                chunk,
                emb.tolist(),
            ))
            total_chunks += 1

    conn.commit()
    cur.close()
    conn.close()
    print(f"\n적재 완료: {total_chunks}청크 저장")


if __name__ == "__main__":
    embed_and_store()
```

### 실행

```bash
# .env에 DATABASE_URL 추가
DATABASE_URL=postgresql://user:password@localhost/signal_alpha

# parsers/ 에서 실행
python vector_store.py
```

---

## STEP 6 — RAG 검색 테스트

### 코드 (`test_rag.py`)

```python
import os
import psycopg2
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("BAAI/bge-m3")
DB_URL = os.getenv("DATABASE_URL", "postgresql://localhost/signal_alpha")


def search(query: str, stock_code: str = None, top_k: int = 5):
    """쿼리와 가장 관련 있는 청크 검색"""
    q_emb = model.encode([query], normalize_embeddings=True)[0].tolist()

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    if stock_code:
        cur.execute("""
            SELECT firm, date, report_type, title, chunk_text, target_price, opinion,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM report_chunks
            WHERE stock_code = %s
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """, (q_emb, stock_code, q_emb, top_k))
    else:
        cur.execute("""
            SELECT firm, date, report_type, title, chunk_text, target_price, opinion,
                   1 - (embedding <=> %s::vector) AS similarity
            FROM report_chunks
            ORDER BY embedding <=> %s::vector
            LIMIT %s
        """, (q_emb, q_emb, top_k))

    rows = cur.fetchall()
    conn.close()
    return rows


if __name__ == "__main__":
    results = search("삼성전자 목표주가 하반기 전망", stock_code="005930")

    for r in results:
        firm, date, rtype, title, chunk, price, opinion, sim = r
        print(f"\n[{firm}] {date} | {rtype} | {title}")
        print(f"  목표주가: {price} | 투자의견: {opinion} | 유사도: {sim:.3f}")
        print(f"  {chunk[:150]}...")
```

### 테스트 쿼리 3개

```bash
# 프로젝트 루트에서 실행
python test_rag.py
```

| 쿼리 | 기대 결과 |
|---|---|
| `"삼성전자 하반기 목표주가"` | 유진/미래에셋 삼성전자 리포트, 목표주가 있는 청크 상위 노출 |
| `"SK하이닉스 HBM 전망"` | 유진 SKH 리포트의 HBM 언급 청크 상위 노출 |
| `"네이버 AI 수익화 근거"` | 미래에셋/유진 네이버 리포트의 AI 관련 청크 상위 노출 |

**검증 기준**: 유사도 0.7 이상이 top-3에 포함되면 임베딩 품질 양호로 판단

---

## 폴더 구조 (완성 후)

```
signal-alpha/
├── crawlers/
│   └── naver_report_crawler.py
├── parsers/                         ← PHASE 5~6 작업 폴더
│   ├── __init__.py
│   ├── pdf_extractor.py             ✅ 완료
│   ├── llm_parser.py                ✅ 완료
│   ├── run_parser.py                ✅ 완료
│   ├── chunker.py                   🔲 미생성
│   └── vector_store.py              🔲 미생성
├── data/
│   ├── reports/
│   │   ├── samsung/                 ✅ 8개 PDF
│   │   ├── skhynix/                 ✅ 2개 PDF
│   │   └── naver/                   ✅ 4개 PDF
│   ├── report_list.json             ✅ 완료
│   └── parsed_reports.json          ✅ 14건 파싱 완료
├── test_rag.py                      🔲 미생성
└── .env                             OPENAI_API_KEY / DATABASE_URL
```

---

## 지금 할 일 체크리스트

### STEP 1~3 (파싱) — 완료

- [x] `parsers/` 폴더 생성
- [x] `pip install pymupdf openai python-dotenv` 설치
- [x] `.env` 파일에 `OPENAI_API_KEY` 설정
- [x] `pdf_extractor.py` 작성
- [x] `llm_parser.py` 작성
- [x] `run_parser.py` 작성
- [x] 전체 14건 파싱 실행 완료
- [x] `data/parsed_reports.json` 생성 확인

### STEP 3.5 (품질 검증) — 완료

- [x] `mirae_20250801_cr.pdf` `target_price: 100 → 88,000` 수정
- [x] null 케이스 7건 수동 교정 (PDF 공시 테이블에서 실제값 추출)
  - `mirae_20250729_cr.pdf` → 78,000 / buy
  - `mirae_20250801_cr.pdf` → 88,000 / buy
  - `mirae_20250814_cr.pdf` → 310,000 / buy
  - `mirae_20250915_cr.pdf` → 96,000 / buy
  - `mirae_20250918_cr.pdf` → 340,000 / buy
  - `mirae_20250922_cr.pdf` → 111,000 / buy
  - `mirae_20250714_cr.pdf` (SKH) → 300,000 / neutral

### STEP 4 (청킹 코드) — 완료

- [x] `pip install sentence-transformers langchain-text-splitters` 설치 확인
- [x] `parsers/chunker.py` 작성
- [x] `parsers/vector_store.py` 작성
- [x] `test_rag.py` 작성 (프로젝트 루트)
- [x] dry-run으로 예상 청크 수 확인 (265청크)

### STEP 4.5 (PostgreSQL 설치) — 미완료 ← 지금 여기

- [ ] PostgreSQL 설치 (Docker 또는 직접 설치)
- [ ] `signal_alpha` DB 생성 + `CREATE EXTENSION vector`
- [ ] `report_chunks` 테이블 생성
- [ ] `.env`에 `DATABASE_URL` 추가
- [ ] 연결 테스트 통과

### STEP 5~6 (적재 + 테스트) — DB 설치 후 진행

- [ ] `python parsers/vector_store.py` 실행 (265청크 적재)
- [ ] 쿼리 3개 이상 테스트 (`python test_rag.py`)
- [ ] 유사도 0.7 이상 결과 확인

---

## 작업 진행 상태

| PHASE | 내용 | 상태 |
|---|---|---|
| PHASE 1 | 환경 세팅 | ✅ 완료 |
| PHASE 2 | 크롤러 개발 | ✅ 완료 |
| PHASE 3 | 리포트 분류 | ✅ 완료 |
| PHASE 4 | PDF 수집 | ✅ 14건 완료 (신한 5건 보완 예정) |
| PHASE 5 | LLM 파싱 + 품질 검증 | ✅ 완료 (14건, 7건 수동 교정) |
| PHASE 6 | 벡터 DB 적재 | 🔲 PostgreSQL 설치 후 진행 → **지금 여기** |
| PHASE 7 | 배치 스케줄링 | 🔲 미시작 |

---

*팀 LENS — Link · Evidence · Navigate · Signal*
