# 채용공고 데이터 직접 수집 계획 (단기·대량 확보용)

> 작성일: 2026-06-24 · 목적: 단기 프로젝트에서 **짧은 시간에 대량의 채용공고 데이터**를 직접 확보
> 제약: 사람인·잡코리아 **공개 API 키 발급 불가**(제외). 과거 공고 소급 불가(포털은 현재 공고만).

---

## 0. 결론 — 소스 우선순위

| 순위 | 소스 | 키 발급 | 양 | 게시일 | 타깃종목(KOSPI 대기업) 커버 | 비고 |
|---|---|---|---|---|---|---|
| **1** | **워크넷 Open API**(한국고용정보원, data.go.kr) | 무료·즉시 | **대량** | ✅ 등록일/마감일 제공 | ⚠️ 약함(중소·공공 위주) | **메인** — API라 빠르고 게시일 결함 없음 |
| 2 | 공공데이터포털 보조 데이터셋(공채속보·채용행사·민간채용현황) | 무료 | 중 | 일부 ✅ | 일부 | 보완 |
| 3 | 기업 공식 채용사이트 크롤(기존 13개사 크롤러) | 불필요 | 소~중 | ❌(현재 미파싱) | ✅ 정확 | **타깃 종목 보완용** |
| ✗ | 사람인·잡코리아 API | **불가** | — | — | — | 키 미발급 → 제외 |
| ✗ | 사람인·잡코리아 크롤 | 불필요 | 중 | ❌ | △ | 느림·차단위험·게시일 없음. 비권장 |

> **핵심 판단**: "빠르게·대량"은 **크롤링이 아니라 워크넷 API**로 푼다. API는 페이징으로 수만 건을 분 단위에 받고, **등록일/마감일이 들어와 게시일 문제까지 동시에 해결**된다. 단, 워크넷은 대기업 공고가 약하므로 **투자신호용 타깃 종목은 공식사이트 크롤로 보완**한다.

---

## 1. 메인: 워크넷 Open API

### 1-1. 인증키 발급 (무료, 5~10분)
- 공공데이터포털 데이터셋: **"한국고용정보원_워크넷 채용정보 채용목록 및 상세정보"** (data ID `3038225`)
  - https://www.data.go.kr/data/3038225/openapi.do → 로그인 → **활용신청** → 일반 인증키(`serviceKey`) 즉시 발급(활용신청 1.1만건+).
- 또는 워크넷 자체 포털: https://openapi.work.go.kr/ 회원가입 → `authKey` 발급.
- 비용 무료, XML 응답, 실시간 갱신.

### 1-2. 엔드포인트 & 핵심 파라미터
- **목록(List)**: `http://openapi.work.go.kr/opi/opi/opia/wantedApi.do`
  - `authKey`(인증키), `callTp=L`(목록), `returnType=XML`,
  - `startPage`(시작페이지), `display`(페이지당 건수), `region`(지역), `occupation`(직종),
  - `busino`(**사업자등록번호** → 특정 회사 필터), `salTp`/`minPay`/`maxPay`, `regDt`(등록일) 등
- **상세(Detail)**: 같은 base, `callTp=D` + 목록에서 받은 공고 id.
- 응답 날짜 필드: **등록일자(regDt)·마감일자(closeDt)·최종수정일** → 게시일 확보.
- 공식 개발명세서(정확한 태그명·코드표)는 https://www.work24.go.kr/cm/e/a/0110/selectOpenApiIntro.do / https://openapi.work.go.kr/opiMain.do 에서 확인(파라미터·XML 태그가 버전에 따라 다를 수 있어 **실제 응답 1건을 먼저 덤프해 태그 확인** 권장).

### 1-3. 수집 스크립트 골격 (페이징 → CSV)
> 실행 전 `serviceKey`만 채우면 됨. 먼저 1페이지를 덤프해 **실제 XML 태그명을 확인**하고 `parse_item`을 맞춘다.

```python
# collect_worknet.py  (uv run python collect_worknet.py)
import csv, time, sys
import requests
import xml.etree.ElementTree as ET

AUTH_KEY = "여기에_발급받은_인증키"
BASE = "http://openapi.work.go.kr/opi/opi/opia/wantedApi.do"
DISPLAY = 100          # 페이지당(최대치는 명세서 확인)
MAX_PAGES = 200        # 필요량에 맞춰 조정 (100×200 = 2만건)

def fetch(page):
    params = {"authKey": AUTH_KEY, "callTp": "L", "returnType": "XML",
              "startPage": page, "display": DISPLAY}
    r = requests.get(BASE, params=params, timeout=20)
    r.raise_for_status()
    return ET.fromstring(r.content)

def parse_item(el):
    # ⚠️ 실제 태그명은 1페이지 덤프로 확인 후 수정 (예시 태그)
    g = lambda t: (el.findtext(t) or "").strip()
    return {
        "company":  g("company"),
        "title":    g("title"),
        "reg_date": g("regDt"),       # 등록일(게시일)
        "close_date": g("closeDt"),   # 마감일
        "region":   g("region"),
        "salary":   g("sal"),
        "busino":   g("busino"),      # 사업자번호 → 종목매핑 키
        "url":      g("wantedInfoUrl"),
    }

def main():
    rows, seen = [], set()
    for p in range(1, MAX_PAGES + 1):
        root = fetch(p)
        items = root.findall(".//wanted")   # ⚠️ 컨테이너 태그명 확인
        if not items:
            print(f"page {p}: 0건 → 종료"); break
        for el in items:
            rec = parse_item(el)
            key = rec["url"] or (rec["company"], rec["title"])
            if key in seen: continue
            seen.add(key); rows.append(rec)
        print(f"page {p}: 누적 {len(rows)}건")
        time.sleep(0.3)   # 예의상 간격
    with open("worknet_jobs.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys()); w.writeheader(); w.writerows(rows)
    print(f"✅ 저장: worknet_jobs.csv ({len(rows)}건)")

if __name__ == "__main__":
    main()
```

**대량 확보 팁**: 지역(`region`)·직종(`occupation`)·등록일(`regDt`) 구간을 나눠 여러 번 호출하면 페이징 상한을 넘어 더 많이 모을 수 있다. 일일 호출 한도 초과 시 에러 반환되므로 `time.sleep`로 간격 유지.

---

## 2. 보조 소스 (공공데이터포털)
- **공채속보**: `data.go.kr/data/15027228/openapi.do` — 대기업 공채 속보(대기업 커버 보완에 유용).
- **채용행사**: `data.go.kr/data/15031948/openapi.do`.
- **워크넷 공통코드**(지역/직종/자격): `data.go.kr/data/15037287/openapi.do` — 위 파라미터 코드값 매핑에 필요.
- **민간 채용정보 현황**(경기데이터드림): data.gg.go.kr.
- (선택) Kaggle 등 공개 채용 데이터셋 — 영문/글로벌 위주라 국내 종목 매핑은 약함.

---

## 3. 타깃 종목(투자신호용) 보완 — 기존 크롤러
워크넷은 삼성·SK하이닉스·네이버 등 대기업 공고 커버가 약하다. 투자신호에 필요한 **특정 KOSPI 종목**은 이미 구현된 공식사이트 크롤러로 보완:
```bash
docker compose run --rm agent-worker python script/run_daily_hiring_pipeline.py
```
→ is_target 전 기업 × (사람인·잡코리아 키워드 + 자소설 + 공식사이트 13곳). 단 **게시일은 현재 미파싱**(별도 평가문서 `hiring-data-quality-assessment-2026-06-24.md` P1 참고).

---

## 3-B. (추천) 자사 채용사이트 내부 JSON API 직접 호출 — 타깃 종목에 최적

대기업은 자사 채용사이트에 직접 공고를 올린다. 그리고 **"쉽게 대량"의 정답은 HTML 크롤이 아니라
그 사이트가 내부적으로 호출하는 JSON 엔드포인트를 직접 때리는 것**이다.

- 요즘 채용사이트는 React/Vue SPA → 화면 렌더 전에 **백엔드 JSON에서 공고 목록을 fetch**한다.
- 그 JSON을 직접 `requests`로 호출하면: Selenium 불필요(수십 배 빠름·차단 부담↓), 페이징으로 대량,
  구조화(직무·부서·지역), **대개 실제 게시일(posting date) 포함 → 게시일 결함도 해결.**
- 증거: 레포 `sites/jasoseol.py`가 이 방식(`/api/v1/employment_companies?page=N`,
  `company_groups`로 과거공고)이라 **유일하게 실제 게시일·과거공고**를 얻는다.

### 방법 1 — 내부 JSON 엔드포인트 찾기 (회사당 ~2분)
1. 채용페이지 → **F12 → Network → Fetch/XHR 필터** → 새로고침.
2. 공고 목록이 담긴 **JSON 응답** 요청을 찾음(보통 `.../jobs`, `.../recruit/list`, `.../api/...`).
3. 우클릭 **Copy as cURL** → `page`/`offset` 파라미터 증가시키며 반복 호출.
4. JSON에서 제목·게시일·직무·URL 추출 → CSV.

### 방법 2 — 표준 ATS는 공개 JSON API (탐색 불필요)
회사 채용 URL/소스에 아래 키워드가 보이면 토큰만으로 바로 대량 수집:

| ATS | 공개 엔드포인트(`{회사}`=토큰) | 게시일 |
|---|---|---|
| Greenhouse | `https://boards-api.greenhouse.io/v1/boards/{회사}/jobs?content=true` | ✅ |
| Lever | `https://api.lever.co/v0/postings/{회사}?mode=json` | ✅ |
| Workday | `https://{tenant}.wdN.myworkdayjobs.com/wday/cxs/{tenant}/{site}/jobs` (POST) | ✅ |
| SmartRecruiters | `https://api.smartrecruiters.com/v1/companies/{회사}/postings` | ✅ |
| Ashby | `https://api.ashbyhq.com/posting-api/job-board/{회사}` | ✅ |

> 삼성/SK하이닉스 등 국내 대기업은 자체 시스템이라 주로 **방법 1**(내부 JSON 탐색). 스타트업·외국계는 ATS 비율 높아 **방법 2** 즉시 가능.

### 한계
- 회사마다 JSON 구조가 달라 **파서를 회사당 1개**씩 만들어야 함(레포도 사이트별 크롤러 분리 이유).
- 자체 시스템 대기업은 엔드포인트 탐색 필요. robots.txt/ToS·호출 간격 준수.

---

## 4. 수집 데이터 활용 (분석 적재)
단기 프로젝트라면 **DB 적재 없이 CSV/Parquet로 바로 분석**하는 게 가장 빠르다.
- 워크넷 CSV → pandas로 정제(등록일 파싱·중복제거·종목 매핑).
- 종목 매핑: `busino`(사업자번호) 또는 회사명 → `stocks` 테이블(`database/seeds/001_seed_stocks.sql`) 조인.
- (선택) 기존 DB 구조에 넣고 싶으면 `raw_documents`+`hiring_raw_details` 3계층에 매핑(평가문서 §1 참조). 단기엔 비권장(오버헤드).

---

## 5. 권장 실행 순서 (반나절 내)
1. data.go.kr 가입 → 워크넷 채용정보 API 활용신청 → 인증키 발급(즉시).
2. `collect_worknet.py`로 1페이지 덤프 → 실제 XML 태그 확인 → `parse_item` 수정.
3. 지역/직종/등록일 구간을 돌려 수만 건 CSV 확보(게시일 포함).
4. (타깃 종목 필요 시) 공식사이트 크롤 1회 추가.
5. pandas로 정제·종목매핑 → 분석 시작.

---

## 6. 한계/주의
- **워크넷 = 중소·공공 편향** → 대기업/투자 타깃 커버 약함. 종목 시그널이 목적이면 커버리지를 먼저 검증할 것.
- **과거 소급 불가** — 워크넷도 "현재 등록 공고" 중심. 등록일 필드로 최근 N개월은 잡히나, 수년 전 데이터는 제한적.
- 워크넷 응답 XML **태그명·코드표는 명세서 확인 필수**(위 스크립트는 골격, 태그는 실제 응답에 맞춰 수정).
- 일일 호출 한도 존재 → 분할 호출 + 간격 유지.
- 사람인·잡코리아 **크롤링**은 가능하나 느리고 차단·게시일 부재로 "단기 대량"엔 비효율 → 비권장.
```
