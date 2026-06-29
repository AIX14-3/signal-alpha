# 시나리오 02 — 리포트 열람 쿼터 (시드 무료 사용자)

**목표:** 무료 3회 쿼터 소진 → 4회차 402 → `/pricing` 리다이렉트 검증.

**사전조건:**
- 무료 사용자 시드: `uv run --package signal-alpha-main-server python services/main-server/scripts/seed_e2e_user.py`
- `sa_refresh` 쿠키 주입(README "인증 상태 만들기") → 로그인 상태.

**절차:**
1. `/report/[ticker]` 진입 → 쿼터 배지 `무료 N회 남음` 확인(초기 3).
2. "무료로 열람하기" 클릭 → 리포트 잠금 해제, 배지가 1 감소하는지 확인.
3. 서로 다른 종목으로 2번 더 반복(총 3회 소진) → 배지 `무료 0회 남음`.
4. 4번째 다른 종목에서 "무료로 열람하기" 클릭.

**기대 결과:**
- 4회차에서 백엔드가 **402(Payment Required)** → 프론트가 `/pricing` 으로 리다이렉트(+토스트).
- 근거 경로: `issueReport()` → `POST /api/reports/{ticker}/issue`, 402 처리(`apiClient`/리포트 스토어).

**리셋:** 다시 0부터 보려면 `docker compose down -v` 후 재시드, 또는 새 phone/email로 재시드.
