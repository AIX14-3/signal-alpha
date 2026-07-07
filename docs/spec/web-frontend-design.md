# Signal Alpha Web Frontend 디자인 스펙 (신규 기획)

> 기준일: 2026-06-24
> 대상: `web/src/app/globals.css`(Tailwind v4 `@theme`), `web/mockups/`
> 목적: 신규 기획 페이지 세트의 비주얼 디자인을 v20 시안 토큰을 계승해 확정한다. 프론트 스펙의 페이지 인벤토리와 1:1 대응한다.
> 연관 문서: [web-frontend-spec.md](./web-frontend-spec.md), [main-server-api-spec.md](./main-server-api-spec.md)
> 시안 베이스: `web/mockups/signal_alpha_minimal_2026_v20_routing_light.html`(직전 최신) → 신규 `v21+`

---

## 1. 디자인 토큰 (v20/globals.css 계승, 변경 없음)

| 토큰 | 값 | 용도 |
|---|---|---|
| `--color-bg` | `#fbfcfe` | 배경 |
| `--color-surface` | `#ffffff` | 카드/패널 |
| `--color-surface-2` | `#f3f6fb` | 보조 표면 |
| `--color-line` | `#e7ecf3` | 보더 |
| `--color-navy` | `#0f1b33` | 브랜드/본문 |
| `--color-navy-soft` | `#36425c` | 보조 텍스트 |
| `--color-muted` | `#8a97ab` | 힌트/메타 |
| `--color-sky` | `#0ea5e9` | 강조 |
| `--color-sky-deep` | `#0284c7` | 강조 딥 |
| `--color-green` | `#10b981` | 상승/긍정 |
| `--color-red` | `#ef4444` | 하락/부정 |
| `--radius-card` / `--radius-sm` | `18px` / `12px` | 라운드 |
| `--font-sans` | Pretendard Variable | 폰트 |
| `--shadow-card` | `0 1px 2px …, 0 8px 24px …` | 카드 그림자 |
| `--ease-out` | `cubic-bezier(.22,.61,.36,1)` | 이징 |

**원칙**: 신규 색/폰트 도입 금지. 상승=green·하락=red 고정. 애니메이션 배경(`bg-fx`)은 홈 전용, 그 외 `bg-static`. `prefers-reduced-motion` 존중. 라이트 모드.

---

## 2. 컴포넌트 규칙

- **card**: `surface` + `1px line` + `radius-card` + `shadow-card`.
- **brand-grad**: `linear-gradient(135deg, sky, green)` — 종합 게이지/CTA/강조.
- **pill**: 999px 라운드 칩(소스 방향/태그/상태).
- **gauge**: SVG 원형(170×170), sky→green 그라데이션 스트로크, 중앙 큰 숫자 + 라벨.
- **방향 칩**: positive=green, negative=red, neutral/mixed=navy-soft, unknown=muted.
- **구독 배지**: 구독자에게 sky pill "구독 중"(리포트 전체 공개라 열람 잠금·쿼터 배지는 없음).

---

## 3. 페이지별 레이아웃 시안 (프론트 스펙 1:1)

1. **메인/검색(`/`)**: `bg-fx` 히어로 + 중앙 검색창 + 자동완성 드롭다운. v20 라우팅 시안 계승.
2. **리포트(`/report/[ticker]`)**: 상단 종목 헤더 + 종합 게이지(좌) / 요약(우). 하단 **5소스 카드 그리드**(주식정보·DART·채용공고·네이버 키워드·증권사 리포트) 각 방향 칩 + 점수 + 1–2줄 LLM 요약 + "상세 보기". 비로그인 포함 전체 공개(잠금 카드·쿼터·발행 버튼 없음). 구독자에겐 우상단 "구독 중" 배지 + 하단 저널 저장 카드.
3. **소스 상세(`/report/[ticker]/[source]`)**: 소스 헤더(라벨/방향/점수) + LLM 상세 요약 카드 + 원천 데이터(테이블 또는 Recharts). 전체 공개.
4. **로그인/회원가입(`/login`,`/signup`)**: 분리 화면. 큰 "본인인증으로 로그인/가입" 버튼(brand-grad), 아이디/비번 입력 없음. 가입 화면은 약관/위험고지 체크 + 닉네임. 하단 소셜 안내(연동은 로그인 후).
5. **마이페이지(`/mypage`)**: 좌측 탭(회원정보·관심종목·구독·저널·소셜) + 우측 패널. 관심종목 무제한 리스트. 소셜 탭 provider 토글.
6. **가격/결제(`/pricing`)**: 단일 9,900원 구독 카드(저널 등 구독 전용 기능) + 무료 회원 비교. 결제 버튼 → 포트원 결제창.
7. **관리자(`/admin`)**: 로그인 게이트 → 매출 지표(Recharts 카드) + 회원 테이블(검색/페이지네이션) + 구독 관리 액션.

---

## 4. 신규 UI 패턴 요약

| 패턴 | 사용처 | 표현 |
|---|---|---|
| 구독 배지 | 리포트 상단·마이페이지 | sky pill "구독 중" |
| 본인인증 버튼 | 로그인/가입 | brand-grad, 단일 액션 |
| 소셜 연동 토글 | 마이페이지 | provider별 on/off |
| 결제창 트리거 | 가격/마이페이지 | 포트원 일반결제 |

---

## 5. 시안 파일 규칙

- 패밀리 `signal_alpha_minimal_2026_v{N}`, 직전 최신 **v20** → 신규 **v21**부터 증가(저장 전 실제 최신 재확인 후 +1).
- 이번 추가: `signal_alpha_minimal_2026_v21_report_sources.html` — 신규 리포트(5소스 + 소스 상세/본인인증/마이페이지) 패턴을 한 파일에 시연(초기 시안엔 쿼터/블라인드 요소가 있으나 2026-07-07 전체 공개 전환으로 폐기).
- 검증: Edge headless 로 렌더 확인(메모리 패턴). 색/폰트 토큰이 v20·globals.css 와 일치하는지 육안 대조.
