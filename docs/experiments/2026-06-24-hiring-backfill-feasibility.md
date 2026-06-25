# 채용 데이터 2021~2023 backfill 가능성 검수 (삼성전자·SK하이닉스·네이버)

> 일자: 2026-06-24 · 방식: **dry-run 검증만**(운영 DB 미적재·코드 변경·PR 없음)
> 검수 환경: `origin/main`(`6acda5c`) 기준 임시 worktree `sa-hiring-verify`, `uv sync` 완료.
> jasoseol 공개 API는 무인증 → 추가 키 불필요.

## 한 줄 결론

**받아올 수 있다 — 단, `jasoseol`(자소설닷컴) 깊은 backfill 한 경로뿐이고, 그 엔진은 현재
작업 브랜치엔 없고 main에만 있다(=pull 선행). 네이버·SK하이닉스는 양호, 삼성전자는 매우 희소
(자사 포털 채용 위주)하며 자사 포털 크롤러로는 과거 보강이 불가능하다.**

## 1. 왜 pull이 선행 조건인가

- 작업 브랜치 `feat/altdata-per-source-final-signals`(HEAD `94f878b`)는 `origin/main`의 **조상**(단순 뒤처짐).
- 과거 backfill 엔진 `sites/jasoseol.py` + `scripts/backfill_jasoseol_history.py`는 **PR #376로 main에만 병합**.
  → main을 받아야(=worktree/merge) 과거 수집이 가능. 본 검수는 main 기준 worktree에서 수행.

## 2. 소스 × 종목 가능/불가 매트릭스 (2021~2023 과거치)

| 소스 | 과거치 지원 | 삼성전자(005930) | SK하이닉스(000660) | 네이버(035420) |
|---|---|---|---|---|
| **jasoseol 깊은 backfill** (id 범위 스캔) | ✅ 2020~ | ✅ 가능하나 **희소** | ✅ | ✅ |
| jasoseol `crawl_history()` | ⚠️ 최근 ~2년 하드캡 | △ | △ | △ |
| saramin / jobkorea | ❌ 현재 스냅샷만 | ❌(시드 제외) | (현재만) | (현재만) |
| 자사사이트 크롤러 | ❌ 현재만 | ❌ **공고 0건(안내만)** | (현재만) | (현재만) |

**→ 2021~2023 과거 시계열을 주는 소스는 `jasoseol` 깊은 backfill 단 하나.**

## 3. 검수 실측 (probe 결과)

### Probe A — API가 2021~2023 구간을 실제로 준다 (결정적 PASS)
- 라이브 `find_max_id()` = **104,776**
- `find_boundary_id('2021-01-01')` = **id 41,138** (실 게시일 2021-01-04 확인)
- `find_boundary_id('2024-01-01')` = **id 90,228**
- **2021~2023 창 = id 41,138 ~ 90,227 (약 49,090 ids)**
- 창 균등 표본의 게시일이 2021-01 → 2023-09로 **단조 증가**, 회사명·제목·게시일 정상 파싱.
  → "API가 그 기간을 실제로 서빙한다" 입증.

### Probe B — 3종목 모두 2021~2023 jasoseol에 존재 (균등표본 ~1.2%)
607점 표본에서 실제 매칭:
- **네이버**: 3건 (2021-04 월간영입, 2021-07, 2022-01) — **풍부**
- **SK하이닉스**: 1건 (2021-08 하반기 신입채용)
- **삼성전자**: 진짜 2건(2022-04) + 오매칭 1건(`삼성전자판매`, 별도회사)
- CLI(`backfill_jasoseol_history.py --dry-run`)도 3종목 **오류 없이** 동작
  (경계 자동탐색·`--until` 상한·`--start-id` 재개 모두 확인).

> ⚠️ **표본 부재 ≠ 전수 부재.** 삼성은 희소(이전 **전수 2021 적재에서 7건**)라 1.2% 표본엔 거의 안 잡힘.
> 정확한 연도별 건수는 전수 스캔이라야 나온다(=실제 적재 단계).

### Probe C — 삼성 자사 포털 보강 불가 확인
- `SamsungCrawler().crawl('삼성전자')` = **'채용 안내' 1건뿐(공고 0건, 날짜=오늘)**.
- 신 포털 `samsungcareers.com/hr/`는 관계사 선택형 디렉터리라 삼성전자 자체 공고 미노출 → 과거 보강 불가.

## 4. 주의 — 이름 퍼지매칭 오버매칭

backfill 사전필터(`_target_matcher`/`_norm`)는 부분일치라 **오매칭** 발생:
`삼성전자판매→삼성전자`, `굿네이버스/네이버클라우드→네이버`. 코드 주석대로 **적재 단계
`_resolve_stock`(stocks 권위 매칭)이 최종 필터**이므로 dry-run의 `종목매칭` 수는 **과대**.
→ 실제 적재 건수는 전수 스캔 + 적재단계 해석으로만 확정.

## 5. 비용 / 운영 (실제 적재 시)

- 전수 대상 ≈ **49,090 id GET**(2021~2023). pause 0.3s 기준 ≈ **6시간**(예의), pause 0이면 ~2시간.
- **재개 가능**(`--start-id`), `--max-requests` 안전상한, `--batch-size` 적재배치.
- prod 스키마는 이전 2021 적재 때 hiring 부분정합(`hiring_raw_details.observed_date` 추가 +
  `hiring_quarantine` 생성) 완료 → 동일 스키마로 2021~2023 적재 가능. (타 테이블 stale 여부는 팀 공유 사안.)

## 6. 권고 (사용자 승인 시 실제 적재)

```bash
# main 기준 worktree에서, DATABASE_URL(prod) 설정 후 — 전 is_target 3년 전수
uv run python scripts/backfill_jasoseol_history.py --since 2021-01-01 --until 2024-01-01
# 중단 시 직전 로그의 start-id 로 재개
uv run python scripts/backfill_jasoseol_history.py --start-id <N> --until 2024-01-01
```
- `--company` 미지정 시 DB `is_target` 전체(3종목 포함) 자동 대상.
- **삼성전자 한계**: 결과는 희소(연 한 자릿수~수십). 시계열 신호로 쓰기엔 표본 부족 → 보고서에 한계 명시 권장.
  삼성 과거 채용 대안(DART 채용공시·사업보고서 직원수 등)은 **대체데이터 스코프 밖** → 별도 결정.

## 부록 — 검수 산출물/정리
- 임시 probe: worktree `sa-hiring-verify`의 `_probe_jasoseol.py`·`_probe_targets.py`(throwaway).
- worktree 정리: `git worktree remove sa-hiring-verify` (검수 후).
