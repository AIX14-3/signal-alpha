# 시나리오 04 — 구독중(무제한) 상태 UI (시드 구독 사용자)

**목표:** 구독 활성 사용자가 무제한 열람/구독 상태 UI를 올바르게 보는지 검증.
실 결제 위젯은 자동화 불가 → **구독 상태는 결제 우회로 시드**한다.

**사전조건:**
- 구독 사용자 시드:
  `E2E_SUBSCRIBE=1 uv run --package signal-alpha-main-server python services/main-server/scripts/seed_e2e_user.py`
- `sa_refresh` 쿠키 주입(로그인 상태).

**절차:**
1. `/report/[ticker]` 진입 → 쿼터 배지가 `구독 중 · 무제한` 으로 표시되는지 확인.
2. "리포트 열람"으로 여러 종목을 연속 열람 → 쿼터 차감 없이 모두 열리는지 확인.
3. 소스 detail(`/report/[ticker]/[source]`)에서 5개 소스 모두 접근 가능한지 확인.
4. `/mypage` → 구독 탭에서 상태 active / 만료일 표시 확인.
5. `/pricing` 진입 → 이미 구독 중이면 결제 CTA 대신 구독중 안내가 보이는지 확인.

**기대 결과:**
- 구독 사용자는 402가 발생하지 않고, 모든 소스 detail 접근 가능.
- `GET /api/subscriptions/me` 가 active 구독을 반환.

**참고:** 결제 플로우(checkout→PortOne 위젯→confirm) 자체는 real 전용이라 이 시나리오로 검증하지 않는다.
별도로 수동 관찰만 한다.
