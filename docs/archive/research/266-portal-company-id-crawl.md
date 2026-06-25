# [Spike #266] 포털(사람인/잡코리아) 기업ID 직접 크롤 지원 여부

**결론 한 줄**: **잡코리아 = 지원 O**(기업ID 기반 공고 리스트가 서버렌더로 깨끗이 제공됨 → 구현 승격 권장).
**사람인 = httpx로는 봇 게이트에 막힘**(데스크톱·모바일 동일 JS 셸) → 실브라우저 기반 별도 후속 필요, 현행 유지.

> 연구 일자 2026-06-19. 라이브 read-only GET 탐침(throwaway 스크립트, 미커밋). 대표 종목 삼성전자·SK하이닉스 등.
> 크롤러 코드 변경 0. #176 항목3(포털 기업ID 직접 크롤)에서 분리된 스파이크.

## 판정 요약

| 포털 | 기업ID 공고리스트 | 식별자 | 렌더 방식 | 차단(httpx) | 판정 |
|---|---|---|---|---|---|
| **잡코리아** | **있음** `/Recruit/Co_Read/Recruit/C/<id>` | 잡코리아 내부 **회원번호**(예 19481) | **서버렌더 HTML**(GI_Read 링크) | 없음(~15회 무차단) | **O — 승격 권장** |
| **사람인** | 미확인 | (csn 추정, 미확인) | SPA(JS 게이트) | **전면 차단**(데스크톱·모바일 동일 셸) | △ — 실브라우저 후속 |

---

## 잡코리아 — 지원 O (증거)

### 패턴
1. **기업 식별자 획득**: 임의 공고 상세 `/(Recruit/)GI_Read/<jobId>` HTML에 고용/관련사 링크가 `Co_Read/C/<회원번호>` 형태로 박혀 있다.
2. **기업 공고 리스트(핵심)**: `GET https://www.jobkorea.co.kr/Recruit/Co_Read/Recruit/C/<회원번호>`
   → **그 기업의 진행 중 공고가 서버렌더 HTML**로 내려온다(`/Recruit/GI_Read/<id>` 링크 + 건수). XHR 불필요.

### 실측 캡처 (2026-06-19, status 200)
| 회원번호 | `<title>` | 서버렌더 GI_Read 수 |
|---|---|---|
| 21493847 | `SK하이닉스(주) 채용 - 2026년 진행 중인 공고 총 8건` | 8 |
| 19481 | `현대오토에버㈜ 채용 - 2026년 진행 중인 공고 총 28건` | 28 |
| 279236 | `㈜태원이엔지 채용 - 2026년 진행 중인 공고 총 4건` | 4 |

- **SK하이닉스(우리 추적 종목)** 가 회원번호 `21493847`로 깨끗이 해석됨 → 키워드검색의 협력사·대리점 노이즈 없이 **그 법인 공고만** 수집 가능.
- **데이터 순수도**: 응답이 정상 HTML이고 공고 링크가 기존 [jobkorea.py](../../services/agent-worker/app/collectors/hiring/sites/jobkorea.py)의 `GI_Read` 파서와 동일 패턴 → '가짜 API(HTML 덩어리)' 문제 없음. 정제비용 낮음.
- **anti-block**: 회사 페이지/리스트에 ~15회 연속 요청 동안 403/캡차 0건. 통합검색과 동일 수준으로 느슨.

### PoC 스니펫 (검증된 동작)
```python
import re, httpx
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124.0 Safari/537.36"

def jobkorea_company_postings(member_id: str) -> list[str]:
    """잡코리아 회원번호로 그 기업의 진행 중 공고 ID 리스트(서버렌더)."""
    url = f"https://www.jobkorea.co.kr/Recruit/Co_Read/Recruit/C/{member_id}"
    r = httpx.get(url, headers={"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"},
                  timeout=15, follow_redirects=True)
    r.raise_for_status()
    return sorted(set(re.findall(r"/Recruit/GI_Read/(\d+)", r.text)))

# jobkorea_company_postings("21493847")  -> SK하이닉스 8건의 공고 ID
```

### 승격 시 필요 작업(후속 구현 이슈)
- **stock → 잡코리아 회원번호 매핑** 확보(1회성). 식별자는 csn(사업자번호)이 **아니라** 잡코리아 내부 회원번호 →
  공공데이터로 자동 주입 불가. 종목당 1회 공고 상세에서 `Co_Read/C/<id>` 추출 또는 기업명 검색으로 수기 매핑(15종목, 가벼움).
- 기존 `jobkorea.py`에 "회원번호 기반 수집" 경로 추가(키워드검색과 병행/대체). GI_Read 상세 파싱은 기존 로직 재사용.
- 효과: 잡코리아 측 협력사 노이즈를 **원천 차단**(#176의 "키워드 후처리" 의존 제거). #176의 "80% 감소" 가설은 잡코리아 한정 **타당**으로 확인.

---

## 사람인 — httpx 차단 (증거)

- `GET https://www.saramin.co.kr/zf_user/search/recruit?searchword=삼성전자` → status 200이지만 **14,718바이트 고정 JS 셸**
  (`<title>사람인</title>` + `<style>`만, `<script src>` 0, 공고/회사 데이터 0).
- 존재하지 않는 URL(`?csn=samsung`, `jobs/list/...`)도 **동일한 14,718바이트 셸** 반환 → 라우팅이 클라이언트 JS에 있음(SPA 게이트).
- **모바일 우회 실패**: `m.saramin.co.kr`(모바일 UA)도 **동일 14,718바이트 셸**. Gemini가 제안한 모바일 느슨함이 사람인엔 적용 안 됨.
- 기존 [saramin.py](../../services/agent-worker/app/collectors/hiring/sites/saramin.py)가 이미 **Selenium**을 쓰는 이유와 일치 — 사람인은 실브라우저(JS 실행) 없이는 내용 0.

→ httpx만으로는 사람인의 기업ID(csn 등) 패턴을 **확인 불가**. csn 기반 여부는 Selenium + DevTools 네트워크 캡처로만 검증 가능(별도 후속 스파이크).

---

## 권고

1. **잡코리아: 구현 이슈로 승격** — `/Recruit/Co_Read/Recruit/C/<회원번호>` 기반 기업 직접 수집 + 종목별 회원번호 매핑(15종목 1회성). 노이즈 원천 차단·정제비용 낮음·차단 느슨으로 가성비 높음.
2. **사람인: 현행 유지(키워드검색 + 수집단계 정확매칭 #176)** — httpx 전면 차단 + 모바일도 막힘. 기업ID 확인 자체가 Selenium 네트워크 캡처를 요하는 별도 작업이라, 이번 스파이크 범위에선 "현행이 최선"으로 결론. 필요 시 별도 "사람인 Selenium ID 스파이크" 분리.

## 부록 — 탐침 방법(재현)
throwaway httpx 프로브(미커밋)로: ① 키워드검색 결과·공고상세에서 회사 링크 패턴 역추출 → ② 후보 URL 직접 호출로 기업 스코프 리스트 여부·렌더방식·차단 실측. 잡코리아는 회사 '채용공고' 탭 URL이 페이지 소스(`/Recruit/Co_Read/Recruit/C/<id>`)에 노출돼 XHR 캡처 없이 확인됨.
