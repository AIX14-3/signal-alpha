# Hiring 세부 기술(skill) 보강 설계 — 후속 과제

> 상태: **설계(미구현)**. 본 PR(자소설닷컴 크롤러 + 직군 태그)의 **후속 별도 과제**.
> 본 PR이 직군 수요(`duty_groups`)까지 확보했고, 이 문서는 그 아래 층위(개별 기술)를
> 어떻게 보강할지의 설계만 담는다.

## 1. 배경 / 문제

채용 신호의 목표는 "어느 **직군**을 늘리나(직군 수요)"와 "어떤 **기술**을 요구하나(skill 수요)"다.

- **직군 수요**: 해결됨(본 PR). 자소설 `employments[].duty_group_ids` → 174개 직군
  분류로 태깅(`duty_groups`). 이미지/제목 파싱 없이 구조화 신호 확보.
- **세부 기술**(C#·SQL·ERP/MES·Embedded·ISP·Network protocol …): **미해결**.
  한국 대기업 공채의 자격요건/우대사항은 대부분 **이미지(포스터)** 로 게시된다.
  실측: 자소설 상세 `content` 는 8/8 표본에서 `<img>` 뿐(텍스트 ~0자). 즉 API 텍스트로는
  세부 기술을 알 수 없다.

세부 기술의 원천은 두 곳뿐:
- (가) 공고 포스터 **이미지**(`content` 내 `<img>` / `image_url`)
- (나) 회사 자체 **채용 사이트**(`employment_page_url`) — 본 PR이 레코드에 보존해 둠

## 2. 접근안 비교

| 접근 | 방법 | 장점 | 단점/비용 |
|---|---|---|---|
| **A. 이미지 OCR** | 포스터 이미지 → OCR → 텍스트 → 기술 키워드 추출 | 자소설 데이터만으로 완결, 사이트 다양성 무관 | OCR 인프라(엔진/비용), 표·레이아웃 정확도, 이미지 없는 공고는 불가 |
| **B. 회사사이트 크롤** | `employment_page_url` 방문 → 본문 파싱 | 원문 텍스트(정확) | 사이트마다 구조 상이(수십 종), SPA/로그인/ATS 다양 → 고유지보수, 차단 위험 |
| **C. 직군태그만 유지** | 본 PR 상태 유지 | 비용 0, 안정 | 세부 기술 미확보 |

→ **세부 기술의 ROI 대비 비용이 크다.** 직군 수요만으로도 핵심 신호는 성립하므로,
세부 기술은 **선택적·점진** 보강으로 둔다.

## 3. 권장 설계 (점진)

원천을 **본 PR이 이미 보존**(`extra_payload.employment_page_url`, 향후 `content`/`image_url`)
하므로, 보강은 **수집과 분리된 비동기 enrichment 단계**로 둔다(재처리/replay 가능).

```
[수집(본 PR)]  공고 → raw_documents + extra_payload(duty_groups, employment_page_url, image_url)
                                   │
[enrichment(후속)]  ─ Phase 1: OCR 가능 표본만 이미지 OCR → tech_keywords
                    └ Phase 2: 화이트리스트 회사사이트(고빈도 대기업)만 파서 추가
                                   │
[분석]  job_functions(직군) + (보강 시) skill 키워드 빈도/변화
```

- **Phase 0(본 PR 연장, 경량)**: 수집 시 `content` 의 `image_url` 도 `extra_payload` 에 보존
  (현재 `employment_page_url` 만 보존 → 이미지 URL도 추가). enrichment 입력 확보.
- **Phase 1 — OCR(독립 잡)**: `processing_queue` 에 `ENRICH_HIRING_SKILL` 태스크를 두고,
  이미지가 있는 raw_document 만 OCR → 기술 키워드 사전(예: `base_site.TECH_KEYWORDS` 확장)
  으로 추출 → `signal_metrics`/`extra_payload.tech_keywords` 적재. 실패/이미지없음은 skip.
- **Phase 2 — 회사사이트(화이트리스트)**: 출원량 많은 상위 N개 대기업의 ATS만
  사이트별 어댑터(기존 `sites/` 패턴)로 본문 텍스트 확보. 전수 X, 고빈도만.

## 4. 비범위 / 가드

- **개인정보 미수집**: 자소서·이력서·지원자 식별정보는 어떤 경로에서도 수집하지 않는다.
- **ToS**: 회사사이트 크롤은 각 사이트 약관/robots 확인 후 화이트리스트로만.
- **하드코딩 금지**: 대상 회사는 DB(stocks), 키워드 사전은 설정/사전 파일.
- DART·main-server 영역 불가(스코프 밖).

## 5. 완료 기준(후속 이슈)

- [ ] Phase 0: 수집 시 image_url 보존(extra_payload)
- [ ] Phase 1: OCR enrichment 잡 + 기술 키워드 추출 + 적재(재처리 가능)
- [ ] (선택) Phase 2: 상위 대기업 ATS 어댑터 1~2종 PoC
- [ ] 추출 기술 키워드가 직군(`duty_groups`)과 교차 분석 가능
