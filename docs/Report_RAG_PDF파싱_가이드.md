# Report RAG — PDF 파싱 & 벡터 DB 적재 가이드

> **팀 LENS | Signal α**
> PHASE 5~6: PDF 텍스트 추출 → LLM 파싱 → 벡터 DB 적재

---

## 현재 상태 (2026-06-08 기준)

| 항목 | 상태 |
|---|---|
| 크롤링 목록 | ✅ `data/report_list.json` — 19건 |
| PDF 수동 저장 | ✅ 14개 저장 완료 (신한 5건 미수집) |
| LLM 파싱 | 🔲 미시작 |
| 벡터 DB 적재 | 🔲 미시작 |

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
| 네이버 | 미래에셋증권 | `miae_20250808_er.pdf` | earnings_review ⚠️ 파일명 오타 |
| 네이버 | 미래에셋증권 | `mirae_20250814_cr.pdf` | company_report |
| 네이버 | 유진투자증권 | `eugene_20250811_cr.pdf` | company_report |
| 네이버 | 미래에셋증권 | `mirae_20250918_cr.pdf` | company_report |

> ⚠️ `miae_20250808_er.pdf` → `mirae_20250808_er.pdf` 로 이름 바꾸기 권장

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
STEP 1. PDF 텍스트 추출 (PyMuPDF)
      ↓
STEP 2. LLM 파싱 — 3가지 추출
        목표주가 / 투자의견 / 핵심 근거
      ↓
STEP 3. 결과 JSON 저장 (parsed_reports.json)
      ↓
STEP 4. 청킹 + 임베딩
        500토큰 단위 분할 / BGE-M3 임베딩
      ↓
STEP 5. pgvector 적재
        메타데이터 + 벡터 저장
      ↓
STEP 6. RAG 검색 테스트
        "SK하이닉스 최신 목표주가" 쿼리 테스트
```

---

## STEP 1 — PDF 텍스트 추출

### 라이브러리 설치

```bash
pip install pymupdf openai python-dotenv
```

### 코드

```python
# parsers/pdf_extractor.py
import fitz  # pymupdf
from pathlib import Path


def extract_text(pdf_path: str | Path) -> str:
    """
    PDF에서 전체 텍스트 추출
    한국어 리포트는 pymupdf가 pdfplumber보다 안정적
    """
    doc = fitz.open(str(pdf_path))
    pages = []
    for page in doc:
        text = page.get_text("text")
        pages.append(text)
    doc.close()
    return "\n".join(pages)


def extract_first_pages(pdf_path: str | Path, n: int = 3) -> str:
    """
    앞 n페이지만 추출 (목표주가·투자의견은 대부분 1~3페이지에 있음)
    """
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
from parsers.pdf_extractor import extract_text, extract_first_pages

# 샘플 1개로 먼저 확인
text = extract_first_pages("data/reports/samsung/mirae_20250714_er.pdf", n=3)
print(text[:1000])  # 앞 1000자 출력
# → 한글이 정상적으로 나오는지 확인
# → 표 안의 목표주가 숫자가 보이는지 확인
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

### 프롬프트 설계

```python
# parsers/llm_parser.py
import json
import os
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()
client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

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


def parse_report(text: str, model: str = "gpt-4o-mini") -> dict:
    """
    리포트 텍스트에서 목표주가·투자의견·핵심 근거 추출

    Args:
        text: PDF에서 추출한 텍스트 (앞 3페이지 권장)
        model: 사용할 OpenAI 모델

    Returns:
        {"target_price": int|None, "opinion": str, "key_rationale": str}
    """
    # 토큰 제한: 앞 3000자만 사용 (목표주가·투자의견은 앞부분에 있음)
    truncated = text[:3000]

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": PARSE_PROMPT},
                {"role": "user", "content": f"리포트 텍스트:\n\n{truncated}"}
            ],
            temperature=0,
            response_format={"type": "json_object"}
        )
        raw = response.choices[0].message.content
        result = json.loads(raw)
        return result

    except Exception as e:
        print(f"  [파싱 오류] {e}")
        return {
            "target_price": None,
            "opinion": "unknown",
            "key_rationale": ""
        }
```

---

## STEP 3 — 전체 파싱 실행 및 결과 저장

### 파일-리포트 매핑 테이블

PDF 파일명과 `report_list.json` 항목을 연결해야 합니다.

| PDF 파일명 | report_list.json 매칭 키 |
|---|---|
| `mirae_20250714_er.pdf` | firm=미래에셋증권, date=25.07.14, stock=삼성전자 |
| `eugene_20250709_cr.pdf` | firm=유진투자증권, date=25.07.09, stock=삼성전자 |
| ... | ... |

### 파싱 실행 코드

```python
# parsers/run_parser.py
import json
from pathlib import Path
from pdf_extractor import extract_first_pages
from llm_parser import parse_report

DATA_DIR = Path("data")
REPORTS_DIR = DATA_DIR / "reports"
LIST_PATH = DATA_DIR / "report_list.json"
OUTPUT_PATH = DATA_DIR / "parsed_reports.json"

# 증권사 파일명 코드 → 실제 이름 매핑
FIRM_CODE_MAP = {
    "shinhan": "신한투자증권",
    "mirae":   "미래에셋증권",
    "eugene":  "유진투자증권",
}

# 종목 폴더명 → 종목코드 매핑
FOLDER_CODE_MAP = {
    "samsung": "005930",
    "skhynix": "000660",
    "naver":   "035420",
}


def parse_filename(pdf_path: Path) -> dict:
    """
    파일명에서 증권사, 날짜, 유형 파싱
    예: mirae_20250714_er.pdf
        → firm=미래에셋증권, date=2025-07-14, type=earnings_review
    """
    name = pdf_path.stem  # mirae_20250714_er
    parts = name.split("_")
    firm_code = parts[0]
    date_str = parts[1]  # 20250714
    type_code = parts[2] if len(parts) > 2 else "cr"

    type_map = {
        "er": "earnings_review",
        "en": "event_note",
        "cr": "company_report",
        "ep": "earnings_preview",
    }

    return {
        "firm": FIRM_CODE_MAP.get(firm_code, firm_code),
        "date_raw": f"{date_str[:4]}.{date_str[4:6]}.{date_str[6:]}",
        "report_type": type_map.get(type_code, "company_report"),
        "stock_code": FOLDER_CODE_MAP.get(pdf_path.parent.name, ""),
    }


def run():
    # 기존 report_list 로드
    with open(LIST_PATH, encoding="utf-8") as f:
        report_list = json.load(f)

    results = []

    for folder in REPORTS_DIR.iterdir():
        if not folder.is_dir():
            continue

        for pdf_path in sorted(folder.glob("*.pdf")):
            print(f"\n[파싱] {pdf_path.name}")

            meta = parse_filename(pdf_path)

            # 텍스트 추출 (앞 3페이지)
            text = extract_first_pages(pdf_path, n=3)
            if not text.strip():
                print("  [경고] 텍스트 추출 실패")
                continue

            # LLM 파싱
            parsed = parse_report(text)
            print(f"  목표주가: {parsed.get('target_price')}")
            print(f"  투자의견: {parsed.get('opinion')}")
            print(f"  핵심근거: {parsed.get('key_rationale', '')[:80]}...")

            # report_list에서 매칭 항목 찾기
            matched = next(
                (r for r in report_list
                 if r["firm"] == meta["firm"]
                 and r["stock_code"] == meta["stock_code"]
                 and meta["date_raw"] in r["date"]),
                None
            )

            result = {
                "pdf_file": str(pdf_path),
                "firm": meta["firm"],
                "stock_code": meta["stock_code"],
                "date": meta["date_raw"],
                "report_type": meta["report_type"],
                "title": matched["title"] if matched else "",
                "pdf_url": matched["pdf_url"] if matched else "",
                "target_price": parsed.get("target_price"),
                "opinion": parsed.get("opinion", "unknown"),
                "key_rationale": parsed.get("key_rationale", ""),
                "raw_text_preview": text[:500],
            }
            results.append(result)

    # 저장
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n\n완료: {len(results)}건 파싱")
    print(f"저장: {OUTPUT_PATH}")


if __name__ == "__main__":
    run()
```

### 실행 순서

```bash
# 1. 먼저 단일 파일로 텍스트 추출 테스트
python -c "
from parsers.pdf_extractor import extract_first_pages
t = extract_first_pages('data/reports/samsung/mirae_20250714_er.pdf', 3)
print(t[:800])
"

# 2. LLM 파싱 단일 테스트
python -c "
from parsers.pdf_extractor import extract_first_pages
from parsers.llm_parser import parse_report
t = extract_first_pages('data/reports/samsung/mirae_20250714_er.pdf', 3)
result = parse_report(t)
import json; print(json.dumps(result, ensure_ascii=False, indent=2))
"

# 3. 전체 실행
python parsers/run_parser.py
```

### 기대 출력

```
[파싱] mirae_20250714_er.pdf
  목표주가: 82000
  투자의견: buy
  핵심근거: 2Q25 영업이익이 컨센서스를 소폭 하회했지만 HBM 매출 비중이 빠르게 확대...

[파싱] eugene_20250709_cr.pdf
  목표주가: 95000
  투자의견: neutral
  핵심근거: 범용 메모리 가격 하락 압력이 지속되나 하반기 반등 기대...

...

완료: 14건 파싱
저장: data/parsed_reports.json
```

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

### 코드

```python
# parsers/chunker.py
from langchain_text_splitters import RecursiveCharacterTextSplitter


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """
    텍스트를 청크 단위로 분할
    """
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=overlap,
        separators=["\n\n", "\n", ".", " ", ""],
    )
    return splitter.split_text(text)
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
    embedding   vector(768),        -- BGE-M3: 768차원
    created_at  TIMESTAMP    DEFAULT NOW()
);

CREATE INDEX ON report_chunks USING ivfflat (embedding vector_cosine_ops);
```

### 임베딩 + 적재 코드

```python
# parsers/vector_store.py
import json
import os
import psycopg2
from sentence_transformers import SentenceTransformer
from chunker import chunk_text

model = SentenceTransformer("BAAI/bge-m3")

DB_URL = os.getenv("DATABASE_URL", "postgresql://localhost/signal_alpha")


def embed_and_store(parsed_path: str = "data/parsed_reports.json"):
    with open(parsed_path, encoding="utf-8") as f:
        reports = json.load(f)

    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    total_chunks = 0

    for report in reports:
        # PDF 전체 텍스트 재추출 (청킹은 전체 텍스트 기준)
        from pdf_extractor import extract_text
        full_text = extract_text(report["pdf_file"])

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
```

---

## STEP 6 — RAG 검색 테스트

```python
# test_rag.py
import os
import psycopg2
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("BAAI/bge-m3")
DB_URL = os.getenv("DATABASE_URL", "postgresql://localhost/signal_alpha")


def search(query: str, stock_code: str = None, top_k: int = 5):
    """
    쿼리와 가장 관련 있는 청크 검색
    """
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
    # 테스트 쿼리
    results = search("삼성전자 목표주가 하반기 전망", stock_code="005930")

    for r in results:
        firm, date, rtype, title, chunk, price, opinion, sim = r
        print(f"\n[{firm}] {date} | {rtype} | {title}")
        print(f"  목표주가: {price} | 투자의견: {opinion} | 유사도: {sim:.3f}")
        print(f"  {chunk[:150]}...")
```

---

## 폴더 구조 (완성 후)

```
signal-alpha/
├── crawlers/
│   └── naver_report_crawler.py
├── parsers/                         ← 이번에 만드는 폴더
│   ├── pdf_extractor.py             ← PDF 텍스트 추출
│   ├── llm_parser.py                ← LLM 파싱 (3가지 추출)
│   ├── chunker.py                   ← 청킹
│   ├── vector_store.py              ← pgvector 적재
│   └── run_parser.py                ← 전체 파이프라인 실행
├── data/
│   ├── reports/
│   │   ├── samsung/
│   │   ├── skhynix/
│   │   └── naver/
│   ├── report_list.json             ← 크롤링 목록
│   └── parsed_reports.json          ← LLM 파싱 결과 ← 새로 생성
├── test_rag.py                      ← RAG 검색 테스트
└── .env                             ← OPENAI_API_KEY
```

---

## 지금 할 일 체크리스트

### 먼저 (바로 시작)

- [ ] **파일명 오타 수정**: `miae_20250808_er.pdf` → `mirae_20250808_er.pdf`
- [ ] `parsers/` 폴더 생성
- [ ] `pip install pymupdf openai python-dotenv` 설치
- [ ] `.env` 파일에 `OPENAI_API_KEY` 설정

### STEP 1~3 (파싱)

- [ ] `pdf_extractor.py` 작성 + 단일 파일 텍스트 추출 확인
- [ ] `llm_parser.py` 작성 + 단일 파일 LLM 파싱 확인
- [ ] `run_parser.py` 작성 + 전체 14건 파싱 실행
- [ ] `data/parsed_reports.json` 생성 확인
- [ ] 결과 품질 육안 검증 (3~5건 확인)

### STEP 4~5 (벡터 DB)

- [ ] `pip install sentence-transformers langchain-text-splitters pgvector` 설치
- [ ] PostgreSQL `report_chunks` 테이블 생성
- [ ] `vector_store.py` 작성 + 적재 실행
- [ ] 적재 건수 확인

### STEP 6 (테스트)

- [ ] `test_rag.py` 실행
- [ ] 쿼리 3개 이상 테스트
  - `"삼성전자 하반기 목표주가"`
  - `"SK하이닉스 HBM 전망"`
  - `"네이버 AI 수익화 근거"`
- [ ] 검색 결과 관련성 확인

---

## 작업 진행 상태

| PHASE | 내용 | 상태 |
|---|---|---|
| PHASE 1 | 환경 세팅 | ✅ 완료 |
| PHASE 2 | 크롤러 개발 | ✅ 완료 |
| PHASE 3 | 리포트 분류 | ✅ 완료 |
| PHASE 4 | PDF 수집 | ✅ 14건 완료 (신한 5건 보완 예정) |
| PHASE 5 | LLM 파싱 | 🔲 미시작 → **지금 시작** |
| PHASE 6 | 벡터 DB 적재 | 🔲 미시작 |
| PHASE 7 | 배치 스케줄링 | 🔲 미시작 |

---

*팀 LENS — Link · Evidence · Navigate · Signal*
