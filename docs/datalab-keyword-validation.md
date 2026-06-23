# DataLab 키워드 검색량 검증 관문 + 자동/보류 라이프사이클

LLM(Gemini)이 생성한 DataLab 키워드의 신뢰도를 올리기 위해, 키워드를 DB에 넣기 전
**네이버 실제 검색량으로 대조**해 3단계로 분류한다. 확실한 키워드는 즉시 자동 적재되어
데이터가 묵지 않고, 애매한 소수만 관리자가 검수한다.

> 이 문서는 **백엔드 인계 문서**다. 관리자 페이지(UI)는 별도 웹 트랙에서 만들며, 여기서는
> 그 페이지가 읽고/쓸 DB 상태와 쿼리를 정의한다.

## 1. 전체 흐름

```
[자동 / 매일 스케줄]
  daily_keyword_pipeline.py
    1) refresh 드래프트 생성 (최근 DART 사건 기반, Gemini)
    2) 이미 매핑된 키워드 제거
    3) 검색량 검증 관문 (네이버 DataLab, 읽기 전용)
    4) 티어별 자동 적재
         auto   → review_status='approved', is_active=TRUE   (즉시 수집 대상)
         review → review_status='pending',  is_active=FALSE  (관리자 검수 대기)
         reject → 드롭(적재 안 함)
  run_collectors.py --datalab-only
    5) is_active=TRUE 키워드로만 수집  ← pending은 자동 제외

[사람 / 관리자 페이지]
  review_status='pending' 목록 조회 → 승인/거부
```

수집기는 이미 `is_active=TRUE`만 사용하므로, pending 키워드는 **코드 변경 없이** 수집에서 제외된다.

## 2. 검증 티어 (왜 "검색된 날 수"인가)

네이버 DataLab은 검색량이 0인 날은 데이터 포인트를 아예 반환하지 않고, 단일 키워드 조회는
자기 자신의 최대값을 100으로 정규화한다. 따라서 절대 검색량은 키워드 간 비교가 불가능하지만,
**"윈도 기간 중 실제로 검색된 날의 수(active_days)"는 모델에 의존하지 않는 객관적 신호**다.
아무도 안 치는 지어낸 문구는 active_days가 거의 0, 실재하는 문구는 다수다.

| 티어 | 기본 기준(active_days) | review_status | is_active | 처리 |
| --- | --- | --- | --- | --- |
| auto | ≥ 10 | approved | TRUE | 즉시 수집 |
| review | 3 ~ 9 | pending | FALSE | 관리자 검수 |
| reject | < 3 | (드롭) | — | 적재 안 함 |

API 실패/쿼터 초과 키워드는 자동 승인하지 않고 **review로 라우팅**한다(안전 우선).

### 임계값 환경변수

| 변수 | 기본 | 의미 |
| --- | --- | --- |
| `DATALAB_KW_VALIDATION_WINDOW_DAYS` | 30 | 검증 윈도(일) |
| `DATALAB_KW_AUTO_MIN_ACTIVE_DAYS` | 10 | auto 승격 최소 검색일 |
| `DATALAB_KW_REVIEW_MIN_ACTIVE_DAYS` | 3 | review 최소 검색일(미만은 reject) |
| `DATALAB_KW_VALIDATION_MAX_CALLS` | 500 | 1회 실행 네이버 호출 상한(쿼터 가드) |

## 3. DB 스키마 (migration 024)

`datalab_category_keywords`에 추가된 컬럼:

| 컬럼 | 타입 | 의미 |
| --- | --- | --- |
| `review_status` | VARCHAR(10) | `approved` / `pending` / `rejected` (기본 `approved`) |
| `validation_active_days` | INTEGER | 윈도 내 검색된 날 수 |
| `validation_window_days` | INTEGER | 검증 윈도 길이 |
| `validation_coverage` | NUMERIC(4,3) | active_days / window_days |
| `validated_at` | TIMESTAMPTZ | 검증 실행 시각 |

기존 행은 `review_status='approved'`로 백필되어 동작 변화가 없다. 부분 인덱스
`idx_datalab_category_keywords_pending`(WHERE review_status='pending')로 보류 목록 조회가 빠르다.

## 4. 관리자 페이지용 쿼리

### 보류(pending) 목록 조회

```sql
SELECT dck.category_id,
       dc.name              AS category_name,
       dck.keyword,
       dck.polarity,
       dck.validation_active_days,
       dck.validation_window_days,
       dck.validation_coverage,
       dck.validated_at,
       dck.polarity_rationale
FROM datalab_category_keywords dck
JOIN datalab_categories dc ON dc.id = dck.category_id
WHERE dck.review_status = 'pending'
ORDER BY dck.validated_at DESC NULLS LAST, dck.validation_active_days DESC;
```

### 승인 (수집 대상으로 전환)

```sql
UPDATE datalab_category_keywords
SET review_status = 'approved', is_active = TRUE, updated_at = NOW()
WHERE category_id = $1 AND keyword = $2;
```

### 거부 (수집 제외 유지, 이력 보존)

```sql
UPDATE datalab_category_keywords
SET review_status = 'rejected', is_active = FALSE, updated_at = NOW()
WHERE category_id = $1 AND keyword = $2;
```

> 라이프사이클(소프트 삭제) 원칙: 죽은/거부 키워드는 **DELETE 하지 말고** `is_active=FALSE`로
> 비활성화해 근거를 남긴다. 좋은 키워드는 누적되고, 검증에 반복 탈락한 것만 꺼진다.
> "가장 최근 파일만 참조"가 아니라 **DB가 누적 진실의 원천**이다.

## 5. 수동 실행

```bash
# 단일 종목: 검증 후 티어별 자동 적재(무인)
python datalab_polarity_refresh.py  --ticker 005930 --auto-apply
python datalab_polarity_keywords.py --ticker 005930 --auto-apply

# 사람 검수 적용(전수 approved) — 기존 방식 그대로
python datalab_polarity_keywords.py --ticker 005930 --review-file <path> --apply

# 매일 파이프라인(전체 활성 종목)
python daily_keyword_pipeline.py
python daily_keyword_pipeline.py --ticker 005930 --max-calls 300
```

## 6. 스케줄 (GitHub Actions)

`.github/workflows/datalab-daily.yml` — 매일 06:00 KST(21:00 UTC) cron + 수동(`workflow_dispatch`).
키워드 생성·검증·자동적재 → DataLab 수집 순으로 실행한다.

**활성화 전 필요한 Repository Secrets:**
`DATABASE_URL`, `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`, `GEMINI_API_KEY`
(선택) `GEMINI_MODEL`, `COLLECTOR_VERSION`.

> Secrets 미등록 시 워크플로는 실패하지만 다른 CI에는 영향 없다. 등록 후 `Actions` 탭에서
> 수동 1회(`Run workflow`) 실행으로 검증 권장.

## 7. review 파일 수명

드래프트 감사용 JSON은 `services/agent-worker/keyword_reviews/`에 쌓이며(gitignored),
생성 시마다 `KEYWORD_REVIEW_RETENTION_DAYS`(기본 7일)보다 오래된 파일을 자동 삭제한다.
Actions 실행본은 아티팩트로 14일 보관된다. **보류 키워드의 진실은 파일이 아니라 DB**이므로
파일 만료는 감사 로그 정리일 뿐 키워드 손실이 아니다.
