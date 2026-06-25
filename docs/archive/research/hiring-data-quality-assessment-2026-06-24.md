# 채용(HIRING) 데이터 적재 검증 & 데이터 품질 평가

> 작성일: 2026-06-24 · 대상: ArtRS-ST 채용 크롤러 파이프라인 · 환경: 로컬 Docker(PG16) 컨테이너 내 실크롤
> 목적: "데이터 적재가 가능한가 / 분석에 쓸 만한가"에 대한 검증 결과와 한계·권고 정리

---

## 0. TL;DR

- ✅ **실데이터 적재 자체는 정상 작동한다** — 컨테이너 안에서 실크롤 → DB 적재까지 검증 완료(삼성전자·SK하이닉스 5건, 차단 0, 적재율 100%).
- ❌ 하지만 현재 데이터는 **분석에 바로 쓰기 어렵다.** 핵심 결함 3가지:
  1. **게시일(공고가 언제 올라왔는지)을 알 수 없다** — DB의 `published_at`은 게시일이 아니라 *크롤한 시각*이다.
  2. **과거(작년) 공고를 가져올 수 없다** — 포털·공식사이트는 *현재 열린 공고*만 노출하므로 마감된 과거 공고는 소스에서 사라진다.
  3. **양이 적다** — 현재 열린 공고만 + 이번엔 2개사 테스트라 5건. 노이즈 필터로 26→5.

---

## 1. 무엇이 어디에 저장되는가 (테이블 구조)

크롤 1건은 **3계층**으로 저장된다.

| 테이블 | 역할 | 이번 적재 |
|---|---|---|
| `collector_runs` | 수집 실행 1회 = 1행 (성공/실패·건수) | 1 |
| `raw_documents` (`source_type='HIRING'`) | **공고 1건 = 1행** (제목·URL=`external_id`·`published_at`·중복방지 `source_hash`) | 5 |
| `hiring_raw_details` | **채용 상세** (`keyword`=직무명, `job_category`=업종, `observed_date`, `extra_payload` JSONB) — `raw_documents`와 1:1 FK | 5 |
| `processing_queue` (`task_type='NORMALIZE_HIRING'`) | 적재 시 자동 enqueue되는 후속 정규화 작업 | 5 |

> 데이터 본체 = `raw_documents` + `hiring_raw_details` (JOIN). 가장 풍부한 정보는 `hiring_raw_details.extra_payload`(JSONB).

분석/설정용 보조 테이블(이번엔 비었거나 시드): `hiring_signals`(분석결과, 0)·`hiring_baseline`·`hiring_search_trend`·`hiring_quarantine`(실패격리, 0)·`hiring_sources`(크롤러 시드, 15)·`hiring_portal_company_ids`·`hiring_job_functions(_stocks)`·`hiring_ocr_skills`.

---

## 2. 핵심 결함 ① — 게시일을 알 수 없다 (가장 심각)

**증상**: DB에서 "이 공고가 언제 올라온 것인지" 확인 불가.

실제 적재된 5건의 날짜 필드:

| 필드 | 값(예) | 의미 |
|---|---|---|
| `raw_documents.published_at` | `2026-06-24 13:17:47` | ⚠️ 게시일이 아니라 **크롤한 시각** (5건 모두 적재시각과 동일) |
| `hiring_raw_details.observed_date` | `2026-06-24` | "관측한 날"(크롤 날짜)일 뿐 게시일 아님 |
| `extra_payload.posting_date` | *(빈 값)* | 실제 게시일이 들어가야 하나 **전부 NULL** |
| `extra_payload.closing_date` | `"D-5"`, `"D-12"` | 절대날짜 아닌 **상대 카운트다운 문자열**, 일부만 존재 |

**근본 원인 (코드)**:
- `sites/base_site.py:132` — 모든 사이트 크롤러가 `"posting_date": self.now_iso()`로 채운다. 즉 **실제 게시일을 파싱하지 않고 크롤 시각을 넣는다.**
- `base_collector.py:360-361` — `published_at = posting_date or now()`. 위에서 이미 크롤 시각이라 결국 `published_at`에 크롤 시각이 저장된다.
- **유일한 예외**: `sites/jasoseol.py:382` 과거이력(history) 경로만 실제 게시일(`start_time`/`created_at`)을 채운다. 일반 `crawl()`(현재공고)은 채우지 않는다.

**결론**: 현재 구조에서 게시일은 자소설 과거이력 경로를 제외하면 **소실**된다. `published_at`을 게시일로 신뢰하면 안 된다.

---

## 3. 핵심 결함 ② — 과거(작년) 공고를 가져올 수 없다

**왜 최근 것만 보이나**: 사람인·잡코리아·기업 공식 채용사이트는 **현재 모집 중인 공고만 노출**한다. 마감/만료된 공고는 소스에서 내려가므로, 크롤러가 "작년 공고"를 *뒤늦게 긁어올 방법이 원천적으로 없다.* 크롤이 실패한 게 아니라 **소스에 더 이상 존재하지 않는다.**

**그럼 역사 시계열은 어떻게?** — 설계 의도는 두 가지다:
1. **매일 크롤해 `observed_date`로 누적** → 시간이 지나며 "일자별 채용 강도" 시계열이 쌓인다(=오늘부터 미래로 누적, 과거로 소급은 불가).
2. **네이버 데이터랩 3년 검색트렌드(`hiring_baseline`/`hiring_search_trend`)를 과거 프록시로 사용** → 실제 과거 공고가 없으니 검색량으로 계절 베이스라인을 대신 만든다.
3. **부분적 backfill**: 자소설 과거이력 경로만 일부 과거 공고+실제 게시일 확보 가능.

**시사점**: "작년 채용공고 원본"을 DB에 채우는 것은 (자소설 일부 제외) **불가능**하다. 분석은 "오늘부터 매일 누적" 전제로 설계해야 한다.

---

## 4. 핵심 결함 ③ — 양이 적고 구조가 분석에 부적합

- **양**: 이번은 2개사 bounded 테스트라 5건. 게다가 현재 열린 공고만이라 전 종목을 돌려도 한 시점 스냅샷은 수백 건 규모(누적 전).
- **노이즈 필터**: 수집단계 게이트키퍼가 26건→5건으로 축소(미등록 기업 21건 제거). 정상 동작이나 단건 수량은 더 작아 보임.
- **구조**:
  - 핵심 신호인 게시일이 없음(결함①).
  - 풍부한 텍스트(`job_description`, `tech_stack`)가 `extra_payload` JSONB 안에 비정형으로만 있고 **거의 미활용**(별도 정규화/피처화 안 됨).
  - `closing_date`가 `"D-5"` 같은 상대문자열 → 정렬·집계 불가.
  - `job_count`는 항상 1(이벤트 1건=1행) → 집계는 가능하나 시계열 누적이 전제.

---

## 5. 분석 적합성 평가

| 분석 목적 | 현재 가능? | 메모 |
|---|---|---|
| "언제 올라온 공고인지" 기준 분석 | ❌ | 게시일 미보존(결함①) |
| 과거(작년) 추세 분석 | ❌ | 소스에 과거 공고 없음(결함②). 데이터랩 프록시로 우회 |
| 오늘부터 일자별 채용 강도 누적 | ⚠️ 조건부 | 매일 크롤 누적해야 의미. `observed_date` 기반 |
| 채용 텍스트(직무/기술스택) NLP | ⚠️ 미구현 | payload에 원문은 있으나 정규화·피처화 필요 |
| 계절 베이스라인 대비 spike | ⚠️ | `hiring_baseline` 부트스트랩(네이버키) 선행 필요, 이번 미실행 |

---

## 6. 권고 (분석에 쓰려면 필요한 작업)

**P1 — 게시일 복원 (필수)**
- 각 사이트 크롤러(`sites/saramin.py`·`jobkorea.py`·company/*)에서 **실제 게시일 파싱** 추가. 페이지에 게시일이 없으면 최소한 `closing_date`(`D-N`)를 절대날짜로 환산해 저장.
- `base_site.py`의 `posting_date = now_iso()` 기본값을 "게시일 미상이면 NULL + observed_date 별도"로 분리해, 크롤 시각이 게시일로 오인되지 않게.
- 자소설 history 경로(이미 실게시일 채움)를 다른 소스에도 확장 검토.

**P2 — 과거 데이터 전략 확정**
- 과거 소급은 포기하고 "**오늘부터 매일 누적**"을 공식 전제로. 일배치(`run_daily_hiring_pipeline.py`)를 스케줄(cron/ECS)로 상시 가동.
- 과거 프록시가 필요하면 네이버 데이터랩 `hiring_baseline` 부트스트랩 먼저 실행.

**P3 — 텍스트 피처화**
- `job_description`/`tech_stack`를 정규화·임베딩(BGE-M3 이미 보유)해 분석 피처로. 상세는 `docs/research/recruitment-datalab-model-selection.md` 참고.

**P4 — 수량 확보**
- 전 종목 일배치 + 누적으로 양 확보. 단, AWS 배포 시 데이터센터 IP 차단(아래) 주의.

---

## 7. 이번 검증에서 확인된 것 (적재 자체는 정상)

- 컨테이너(Docker) 안에서 headless chromium으로 실크롤 → `raw_documents`/`hiring_raw_details`에 실데이터 적재 성공(5건, 차단신호 0, 적재율 100%).
- 적재된 URL은 실제 라이브 공고: `jobkorea.co.kr/Recruit/GI_Read/49411197`, `talent.skhynix.com/hub/ko/job/introduce?id=1071` 등.
- 즉 **"적재 가능 여부" = 가능**. 문제는 적재 *내용/구조*(게시일·과거·양)이지 파이프라인 동작이 아니다.

---

## 8. AWS 배포 관련 (별개 메모)

- agent-worker 이미지에 chromium+chromedriver 내장(작업 브랜치 `feat/hiring-crawler-docker-chrome`) → Windows-Docker와 AWS-Docker 동일 동작.
- ⚠️ **최대 변수: AWS 데이터센터 IP의 채용포털(사람인/잡코리아) 차단.** 이번 검증은 가정 IP라 403/429=0이었으나 AWS는 다를 수 있음 → residential 프록시 또는 크롤만 국내 상시머신 분리 후속 검토.

---

## 부록 — 재현/조회 명령

```bash
# 기동
docker compose up -d postgres
docker compose run --rm migrate                 # 마이그레이션+시드

# 전체 실크롤(수집+분석)
docker compose run --rm agent-worker python script/run_daily_hiring_pipeline.py

# 조회 (게시일 문제 확인용)
docker compose exec postgres psql -U signal_alpha -d signal_alpha -x -c "
SELECT rd.source_name, rd.title, rd.external_id,
       rd.published_at,                          -- ⚠️ 크롤 시각(게시일 아님)
       hrd.observed_date,
       hrd.extra_payload->>'posting_date' AS posting_date,   -- 비어있음
       hrd.extra_payload->>'closing_date' AS closing_date    -- 'D-5' 상대값
FROM raw_documents rd JOIN hiring_raw_details hrd ON hrd.raw_document_id=rd.id
WHERE rd.source_type='HIRING' ORDER BY rd.id;"
```
