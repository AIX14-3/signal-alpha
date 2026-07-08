# 특허 이중 소스(KIPRIS + BigQuery) 운영 런북

특허 데이터는 **두 소스**에서 들어오며 **둘 다 매일** 최근 공개분을 수집한다(상보적).

> ## ⚠️ 핵심: 특허는 출원 후 ~18개월 뒤 **공개(publication)** 된다
>
> "오늘 출원된 특허"는 18개월간 비공개라 검색해도 안 나온다. 시장에 정보가 노출되는
> 시점은 **공개일(`publication_date`)** 이므로, 수집·분석·표시의 기준 날짜를 출원일이
> 아닌 **공개일**로 둔다. `patent_raw_details.publication_date`(신규 컬럼,
> `20260707_1000` 마이그) = 공개일. 미상이면 NULL(분석기가 `application_date` 폴백).

| 소스 | 매일 커버리지 | 진입점 | 자격증명 | 자동화 |
|---|---|---|---|---|
| **KIPRIS** | 최근 공개분(출원일 창, 기본 최근 ~800일·env 조절) | `run_collectors.py --patent-only` → `PatentCollector.run()` | `KIPRIS_API_KEY` (월 ~1,000건 쿼터) | ✅ `altdata-collect.yml` (06:30 KST) |
| **BigQuery 매일** | 최근 공개분(공개일 창, 기본 90일) | `scripts/collect_patents_bigquery_daily.py` → `bq_rows_recent_publications()` → `ingest_records()` | GCP ADC + `GOOGLE_CLOUD_PROJECT` | ✅ `altdata-collect-bigquery-daily.yml` (05:30 KST, **시크릿 대기**) |
| **BigQuery 주간** | 과거 대량 이력(출원연도 범위, 장기추이용) | `scripts/backfill_patents_bigquery.py` → `ingest_records()` | 〃 | ⚠️ `altdata-collect-bigquery.yml` (주1회, **시크릿 대기**) |

### KIPRIS 수집 창 — 🔬 SPIKE 필요(라이브 키)
`applicantNameSearchInfo` 의 `startDate`/`endDate` 는 **출원일(AD)** 창이다. 기존 기본값
(어제 1일치)은 18개월 공개 지연 탓에 매일 사실상 0건만 수집했다. 현재는 출원일 창을
`PATENT_KIPRIS_WINDOW_START_DAYS`(기본 800)·`..._END_DAYS`(기본 0)로 넓혀 공개 지연을
덮는다(이미 적재분은 dedup skip). ⚠️ 넓은 창은 대형 출원인 페이지 수가 많아 무료 월쿼터
(~1,000콜)를 넘길 수 있으니 최근 공개분 저비용 확보는 **BigQuery 매일**이 주로 담당한다.
**🔬 확인 필요**: KIPRIS 가 공개일(open date) 검색/정렬을 지원하는지 라이브 키로 검증 →
지원하면 출원일 창 대신 공개일 창으로 바꿔 정확·저비용으로 최근 공개분만 받는다(그때
`kipris_client._build_url` 에 공개일 파라미터 추가).

## 중복 제거 (cross-source dedup)

두 소스의 application_no 형식이 다르다:
- KIPRIS: `1020210012345` (13자리 = 출원종별 2 + 연도 4 + 일련 7)
- BigQuery: `KR-20210012345-A` (국가코드 + 11자리 연도+일련 + kind code)

`app/collectors/patent/application_no.py`의 `canonicalize_application_no()`가 둘을 **11자리 연도+일련 표준키**로 환산하고, `source_hash = make_source_hash("PATENT", canonical)`로 적재한다. 따라서 **같은 특허는 어느 소스로 와도 같은 `source_hash`** → `raw_documents_source_hash_key` UNIQUE 제약이 canonical 특허당 1행만 남긴다. 각 소스의 고유 특허는 각각 insert된다.

### KIPRIS 우선 (source priority)
canonical 특허가 두 소스에 겹치면 **KIPRIS 를 authoritative 로** 남긴다(사용자 정책).
- 우선순위: `_SOURCE_PRIORITY = {"KIPRIS": 2, "GOOGLE_PATENTS": 1}` (`collectors/patent/__init__.py`).
- 동작(`_save_record` → `_promote_source_if_outranked`): 신규는 그냥 insert. 이미 있으면 —
  - 기존이 **GOOGLE_PATENTS** 인데 **KIPRIS** 가 오면 → 기존 행을 KIPRIS 레코드로 **덮어씀**(`source_name`·식별/일시 + `patent_raw_details` 상세·`publication_date` 갱신). 결과 outcome=`updated`.
  - 기존이 **KIPRIS** 이거나 같은 소스면 → 덮어쓰지 않음(기존 skip/F1 재인큐 경로).
- 즉 "중복 삭제 후 KIPRIS 유지"가 순증분으로 수렴한다(별도 정리 배치 없이 KIPRIS 수집이 돌 때마다 승격). `updated_count` 로 승격 건수를 관측한다.

- `external_id`는 소스 원본 문자열을 그대로 보존(추적용).
- ⚠️ 표준키는 출원종별 접두(`10`/`20`)를 버리므로, 같은 연도+일련의 특허(10)와 실용신안(20)은 합쳐진다(실제 충돌 사실상 없음, 허용 트레이드오프).
- **forward-only**: 해시식 변경 이전 적재된 행(native-hash)은 새 행과 합쳐지지 않는다. 적용 시점 prod 특허가 mock·소량이라 영향 무시 가능.

## 장애 격리 (failover)

두 소스는 **독립 진입점**이고 `run()`(KIPRIS=`KIPRIS_API_KEY`만)과 `ingest_records()`(BigQuery=GCP만)는 서로의 자격증명/네트워크를 공유하지 않는다. CI에서는 각 수집 스텝을 `continue-on-error: true`로 분리해, **한 소스가 막혀도(KIPRIS 월 쿼터 소진·BigQuery GCP 장애) 다른 소스는 정상 적재**된다.

- **매일**: KIPRIS(출원일 창) + BigQuery 매일(공개일 창) 둘 다 최근 공개분을 잡는다(독립 워크플로·`continue-on-error` 격리). **주간**: BigQuery 출원연도 백필은 장기 추이용 과거 대량이라 주1회로 분리(daily 경로에 끼우지 않음).

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
