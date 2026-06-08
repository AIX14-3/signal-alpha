# source_hash 생성 규칙

`source_hash`는 `raw_documents`의 중복 저장을 막는 핵심 값입니다. 모든 Collector는 `raw_documents` 저장 전에 `source_hash`를 생성해야 합니다.

## 공통 규칙

- 해시 알고리즘은 SHA256을 사용합니다.
- 결과는 64자리 hex string으로 저장합니다.
- 입력값은 `trim` 처리합니다.
- 가능한 값은 `lower` 처리합니다.
- `NULL`은 빈 문자열로 변환합니다.
- 구분자는 `|`를 사용합니다.
- `source_hash`는 DB가 아니라 애플리케이션 레이어에서 생성합니다.

## Collector별 기준

| Collector | source_hash 입력 기준 |
| --- | --- |
| DART | `DART|receipt_no` |
| Report | `REPORT|stock_id|securities_firm|title|publish_date|pdf_url` |
| Hiring | `HIRING|stock_id|source_name|keyword|title|source_url|published_at` |
| Patent | `PATENT|application_no` |
| DataLab | `DATALAB|stock_id|keyword|observed_date|period_type|device|gender|age_group` |

## Python 예시

```python
import hashlib


def make_source_hash(*parts):
    normalized = [
        "" if part is None else str(part).strip().lower()
        for part in parts
    ]
    raw = "|".join(normalized)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
```

사용 예시:

```python
source_hash = make_source_hash(
    "REPORT",
    stock_id,
    securities_firm,
    title,
    publish_date,
    pdf_url,
)
```
