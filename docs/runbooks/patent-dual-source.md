# 특허 이중 소스(KIPRIS + BigQuery) 운영 런북

특허 데이터는 **두 소스**에서 들어오며, 시간상 상보적이다.

| 소스 | 커버리지 | 진입점 | 자격증명 | 자동화 |
|---|---|---|---|---|
| **KIPRIS** | 최신(어제 1일치) | `run_collectors.py --patent-only` → `PatentCollector.run()` | `KIPRIS_API_KEY` (월 ~1,000건 쿼터) | ✅ `altdata-collect.yml` (06:30 KST) |
| **BigQuery (Google Patents)** | 과거 대량(~18개월 지연) | `scripts/backfill_patents_bigquery.py` → `PatentCollector.ingest_records()` | GCP ADC + `GOOGLE_CLOUD_PROJECT` | ⚠️ `altdata-collect-bigquery.yml` (주1회, **시크릿 `GCP_SA_KEY` 등록 대기**) |

## 중복 제거 (cross-source dedup)

두 소스의 application_no 형식이 다르다:
- KIPRIS: `1020210012345` (13자리 = 출원종별 2 + 연도 4 + 일련 7)
- BigQuery: `KR-20210012345-A` (국가코드 + 11자리 연도+일련 + kind code)

`app/collectors/patent/application_no.py`의 `canonicalize_application_no()`가 둘을 **11자리 연도+일련 표준키**로 환산하고, `source_hash = make_source_hash("PATENT", canonical)`로 적재한다. 따라서 **같은 특허는 어느 소스로 와도 같은 `source_hash`** → `raw_documents_source_hash_key` UNIQUE 제약이 두 번째를 자동 skip한다(겹치면 1건). 각 소스의 고유 특허는 각각 insert된다.

- `external_id`는 소스 원본 문자열을 그대로 보존(추적용).
- ⚠️ 표준키는 출원종별 접두(`10`/`20`)를 버리므로, 같은 연도+일련의 특허(10)와 실용신안(20)은 합쳐진다(실제 충돌 사실상 없음, 허용 트레이드오프).
- **forward-only**: 해시식 변경 이전 적재된 행(native-hash)은 새 행과 합쳐지지 않는다. 적용 시점 prod 특허가 mock·소량이라 영향 무시 가능.

## 장애 격리 (failover)

두 소스는 **독립 진입점**이고 `run()`(KIPRIS=`KIPRIS_API_KEY`만)과 `ingest_records()`(BigQuery=GCP만)는 서로의 자격증명/네트워크를 공유하지 않는다. CI에서는 각 수집 스텝을 `continue-on-error: true`로 분리해, **한 소스가 막혀도(KIPRIS 월 쿼터 소진·BigQuery GCP 장애) 다른 소스는 정상 적재**된다.

- KIPRIS는 일간(최신), BigQuery는 18개월 지연이라 **주기(주1/월1) 백필**이 적합 — daily KIPRIS 경로에 끼우지 않는다.

## BigQuery를 CI에 자동화하기 (워크플로 구현됨 — 시크릿 등록만 남음)

워크플로 `altdata-collect-bigquery.yml`이 추가되어 있다(주1회 cron + `workflow_dispatch`).
`google-github-actions/auth@v2`로 `GCP_SA_KEY`를 ADC로 설정한 뒤
`uv run --with google-cloud-bigquery python scripts/backfill_patents_bigquery.py`를
`continue-on-error: true`로 돌려 KIPRIS 경로와 격리한다. **남은 것은 GCP/GitHub 쪽 설정뿐:**

1. **GCP 서비스계정 생성** — `patents-public-data` 조회 권한(BigQuery Job User + 공개 데이터셋 읽기).
2. 서비스계정 **JSON 키를 리포 Actions Secret `GCP_SA_KEY`**로 등록, `GOOGLE_CLOUD_PROJECT` 시크릿도 등록.
3. 등록 후 **`workflow_dispatch`를 `dry_run=true`(기본)로 1회 실행**해 인증·조회를 안전 검증
   (DB 쓰기 없음). 통과하면 스케줄(또는 `dry_run=false` 수동)로 실적재.
4. `bq_rows`/`build_records`는 `app/collectors/patent/bigquery_source.py`에 있으므로 별도 드라이버에서도 재사용 가능.

> ⚠️ `GCP_SA_KEY` 미등록 상태에서는 auth 스텝이 실패한다(워크플로는 `continue-on-error`로 격리되어 잡 자체는 통과). 등록 전까지 이 워크플로의 백필 스텝은 no-op로 본다.

## 검증

```bash
cd services/agent-worker
# 1) 표준화 단위 테스트
uv run pytest tests/test_patent_application_no.py -q
# 2) (GCP 환경) 실제 BigQuery application_number 형식 확인 + 표준화 매핑 검증
uv run --with google-cloud-bigquery python scripts/backfill_patents_bigquery.py --dry-run --limit-per-stock 5
#    → 출력된 application_no 샘플에 canonicalize_application_no() 적용 결과가
#      같은 특허의 KIPRIS 13자리 번호와 일치하는지 확인. 불일치 시 규칙 보정.
```
