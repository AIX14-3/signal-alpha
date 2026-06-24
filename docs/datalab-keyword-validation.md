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
    1) 사건 수집: 네이버 뉴스(최근 14일) + DART 공시(있으면)  ← --source {news,dart,both}
         (+ 그라운딩: 헤드라인을 Gemini Google-Search로 사실 보충 ← --ground)
    1') refresh 드래프트 생성 (위 사건 기반, Gemini)
    2) 이미 매핑된 키워드 제거
    3) 검색량 검증 관문 (네이버 DataLab, 읽기 전용) + 스파이크 패스트트랙
    4) 티어별 자동 적재
         auto   → review_status='approved', is_active=TRUE   (즉시 수집 대상)
         review → review_status='pending',  is_active=FALSE  (관리자 검수 대기)
         reject → 드롭(적재 안 함)
  run_collectors.py --datalab-only
    5) is_active=TRUE 키워드로만 수집  ← pending은 자동 제외

[사람 / 관리자 페이지]
  review_status='pending' 목록 조회 → 승인/거부
```

### 1.1 사건 소스 (뉴스 + DART)

키워드의 "씨앗"은 종목에 일어난 **사건**이다. 사람들은 DART 공시 자체보다 **뉴스로 사건을
접하고 검색**하므로, 기본 사건 소스는 **네이버 뉴스 검색 API**(`news_event_source.py`)이며
DART 공시도 합칠 수 있다(`--source both`, 기본값). 뉴스 항목은 DART 공시와 동일한 이벤트
dict(`report_name`/`disclosure_type`/`priority_reason`)로 매핑되어 이후 단계가 그대로 재사용된다.

뉴스 제목+요약만으로는 본문 디테일(고유명사·수치·제품명)이 빠지므로, `--ground`(기본 on)이면
종목당 1회 **Gemini Google-Search 그라운딩**으로 "사건 상세 브리프"를 만들어 생성 입력에 더한다.
그라운딩은 best-effort라 실패해도 원본 뉴스로 폴백한다. (그라운딩과 JSON 강제출력은 동시 사용이
불가하므로, 그라운딩은 텍스트만 받고 키워드 JSON 생성은 기존 호출이 담당한다.)

> 경계: 이 단계는 여전히 키워드 생성기(pre-collection 도구)다. 뉴스 API는 메모리에서만 읽으며
> `raw_documents` 등 수집 테이블에는 쓰지 않는다.

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

### 2.1 스파이크 패스트트랙 (신선도)

active_days 기준만으로는 "오래 검색돼온 단어"에 유리하고 **속보성 신규 키워드가 누락/지연**된다.
DataLab 검색량은 일 단위라 같은 날 여러 번 돌려도 데이터가 같으므로(전일 확정), 생성 주기 단축은
효과가 없다. 대신 검증 시 이미 받아오는 **일별 검색량 시계열**(추가 API 호출 0)로 "최근 급상승"을
판정해, active_days가 낮아도 **최근에 집중·급상승한 키워드는 auto로 패스트트랙**한다.

판정: 최근 `RECENT_DAYS`(기본 7일) 내 활성일이 `MIN_RECENT_ACTIVE`(기본 2) 이상이고, 윈도 전체
검색량 중 최근 구간 비중(`recent_share`)이 `MIN_RECENT_SHARE`(기본 0.6) 이상이면 급상승으로 보고
승격한다. 오래된 1회성 블립은 recent_share가 낮아 승격되지 않는다. 파이프라인 요약의
`spike_fasttrack=N`이 이렇게 승격된 키워드 수다.

#### 신선도를 더 올리려면 — 스파이크 임계값 공격적 튜닝 (향후 레버)

> **생성 빈도를 더 늘리는 것보다 이쪽이 기능적으로 더 직접적인 신선도 레버다.** 검색량 데이터가
> 일 단위·전일 확정이라 "생성을 더 자주" 해도 *확정 승인*은 못 앞당기지만, 스파이크 임계값을
> 낮추면 **갓 터진 키워드를 더 빨리 auto로 활성화**할 수 있다. 더 실시간이 필요해질 때 먼저 이
> 노브부터 조정한다. 코드 변경 없이 env만으로 가능(롤백도 즉시).

| 노브 | 기본 | 공격적(↑신선도) | 효과 / 주의 |
| --- | --- | --- | --- |
| `DATALAB_KW_SPIKE_MIN_RECENT_ACTIVE` | 2 | **1** | 최근 1일만 검색돼도 급상승 인정 → 속보 즉시 활성. 노이즈/단발 블립↑ |
| `DATALAB_KW_SPIKE_MIN_RECENT_SHARE` | 0.6 | **0.5** | 최근 집중도 요건 완화 → 더 잘 승격. 오래된 키워드 오승격 위험↑ |
| `DATALAB_KW_SPIKE_RECENT_DAYS` | 7 | **3~5** | "최근" 창을 좁혀 더 최신성 위주 판정 |

- 공격적으로 갈수록 **신선도↑ / 정밀도(노이즈 내성)↓** 트레이드오프다. 운영하며 pending/reject
  비율과 잘못 승격된 키워드를 보고 점진 조정한다.
- 더 근본적인 실시간화는 **이벤트 드리븐 트리거**(신규 고관심 사건 감지 시에만 생성)지만 별도
  작업이며, 검증이 일 단위인 하한은 여전히 남는다. (후속 이슈 후보)

### 임계값 환경변수

| 변수 | 기본 | 의미 |
| --- | --- | --- |
| `DATALAB_KW_VALIDATION_WINDOW_DAYS` | 30 | 검증 윈도(일) |
| `DATALAB_KW_AUTO_MIN_ACTIVE_DAYS` | 10 | auto 승격 최소 검색일 |
| `DATALAB_KW_REVIEW_MIN_ACTIVE_DAYS` | 3 | review 최소 검색일(미만은 reject) |
| `DATALAB_KW_VALIDATION_MAX_CALLS` | 500 | 1회 실행 네이버 호출 상한(쿼터 가드) |
| `DATALAB_KW_SPIKE_RECENT_DAYS` | 7 | 급상승 판정 최근 구간(일) |
| `DATALAB_KW_SPIKE_MIN_RECENT_ACTIVE` | 2 | 급상승 최소 최근 활성일 |
| `DATALAB_KW_SPIKE_MIN_RECENT_SHARE` | 0.6 | 최근 구간 검색량 비중 하한 |

### 사건 소스 / 그라운딩 환경변수

| 변수 | 기본 | 의미 |
| --- | --- | --- |
| `NEWS_LOOKBACK_DAYS` | 14 | 뉴스 조회 윈도(일) |
| `NEWS_MAX_ITEMS` | 20 | 종목당 뉴스 최대 건수 |
| `KEYWORD_GROUNDING` | on | 그라운딩 기본 on/off (`--ground/--no-ground`로 재정의) |
| `GEMINI_GROUNDING_MODEL` | (=`GEMINI_MODEL`) | 그라운딩 호출 모델 override |

> 뉴스 API는 기존 `NAVER_CLIENT_ID/SECRET`을 재사용한다. 단, 네이버 개발자 콘솔에서 해당 앱에
> **"검색" API가 활성화**돼 있어야 한다(데이터랩과 별개 스코프). 미활성 시 뉴스 호출이 401(코드 024).

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

# 매일 파이프라인(전체 활성 종목) — 기본 source=both, ground=on
python daily_keyword_pipeline.py
python daily_keyword_pipeline.py --ticker 005930 --max-calls 300

# 사건 소스/그라운딩 선택
python daily_keyword_pipeline.py --ticker 005930 --source news --ground      # 뉴스만 + 그라운딩
python daily_keyword_pipeline.py --ticker 005930 --source dart --no-ground    # DART만, 그라운딩 끔
```

## 6. 스케줄 (GitHub Actions)

`.github/workflows/datalab-daily.yml` — 생성과 수집의 주기가 다르다:

| 시각(KST) | cron(UTC) | 키워드 생성 | 검색량 수집 |
| --- | --- | --- | --- |
| 06:00 | `0 21 * * *` | ✅ | ✅ |
| 14:00 | `0 5 * * *` | ✅ | — |
| 22:00 | `0 13 * * *` | ✅ | — |
| 수동(`workflow_dispatch`) | — | ✅ | ✅ |

- **키워드 생성은 하루 3회** — 뉴스는 실시간이라 신규 사건을 더 빨리 후보화한다. 매 실행은
  새 키워드만 드래프트(`drop_existing`)하고 `ON CONFLICT` 업서트라 행이 폭증하지 않는다.
- **검색량 수집은 06:00 1회만** — DataLab 검색량은 일 단위·전일 확정이라 더 자주 받아도 같은
  데이터(중복 호출·저장 낭비). 수집 단계는 06:00 스케줄 또는 수동 실행에서만 돈다
  (`if: github.event.schedule == '0 21 * * *' || workflow_dispatch`).
- 신규 사건 키워드의 "확정 승인"은 검색량 데이터가 쌓여야 하므로(일 단위), 생성을 더 자주 해도
  검증 신선도엔 하한이 있다 — 그 간극을 스파이크 패스트트랙(§2.1)이 메운다.

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
