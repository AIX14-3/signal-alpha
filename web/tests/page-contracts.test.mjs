import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

const ROOT = process.cwd();

function read(relativePath) {
  return readFileSync(join(ROOT, relativePath), "utf8");
}

function assertIncludes(source, expected, context) {
  assert.ok(
    source.includes(expected),
    `${context} should include ${JSON.stringify(expected)}`,
  );
}

test("pricing page keeps payment flow and login guard wired", () => {
  const source = read("src/app/pricing/page.tsx");

  [
    'data-page="pricing"',
    "listPlans()",
    "checkout(cycle)",
    "pay({",
    "confirmPayment({ payment_id })",
    'router.push("/login")',
    "useAuthStore",
    "useToastStore",
  ].forEach((expected) => assertIncludes(source, expected, "pricing page"));
});

test("report page keeps ticker loading and watchlist wired (full public report)", () => {
  const source = read("src/app/report/[ticker]/page.tsx");

  [
    'data-page="report"',
    "useParams<{ ticker: string }>()",
    "useReportStore",
    "load(ticker)",
    "WatchlistButton",
    "SOURCE_ORDER",
    // AI 예측률(PredictionRateCard)은 근거 중심 재구성에서 제거됨 — PREDICTION_RATE_ORDER 미사용.
    'data-flow="methodology-link"',
    'href="/methodology"',
  ].forEach((expected) => assertIncludes(source, expected, "report page"));
});

test("report wires the S6 precedent section to the forward-return outcome API", () => {
  const page = read("src/app/report/[ticker]/page.tsx");
  [
    "PrecedentSection",
    'id="sec-precedent"',
  ].forEach((expected) => assertIncludes(page, expected, "report page"));

  const section = read("src/components/PrecedentSection.tsx");
  [
    "getReportPrecedents",
    'data-section="precedents"',
    "avg_abnormal_return",
    "horizon_days",
  ].forEach((expected) => assertIncludes(section, expected, "precedent section"));

  assertIncludes(read("src/components/ReportSideNav.tsx"), '"sec-precedent"', "report side nav");
});

test("brief page loads the public daily-signal feed and links to reports", () => {
  const source = read("src/app/brief/page.tsx");

  [
    'data-page="brief"',
    "getBrief(",
    'data-flow="brief-card"',
    "/report/${encodeURIComponent(code)}",
    "directionLabel",
    "warningLevelLabel",
  ].forEach((expected) => assertIncludes(source, expected, "brief page"));
});

test("watchlist page tracks change, not just bookmarks", () => {
  const source = read("src/app/watchlist/page.tsx");

  [
    'data-page="watchlist"',
    // 브리핑과 다른 정렬축: 절대 점수 세기가 아니라 변화량(Δ)이 큰 순.
    "getSignalsByStockIds(stockIds)",
    "Math.abs(a.item.change.score_delta ?? -1)",
    // 소스별 막대 그래프 — 브리핑 카드에 없는 정보(두 화면을 가르는 축).
    "SourceBars",
    // 막대는 0이 아니라 중립 50을 기준으로 갈린다. 50.0 을 방향색으로 칠하면 없는 방향이 생긴다.
    "const NEUTRAL_SCORE = 50",
    "const NEUTRAL_BAND = 0.5",
    "Math.abs(offset) < NEUTRAL_BAND",
    'data-flow="watchlist-card"',
  ].forEach((expected) => assertIncludes(source, expected, "watchlist page"));

  // Δ 가 null 이면 "변화 없음" 이 아니라 **비교 불가** 다 — 0 과 구분해야 한다.
  assertIncludes(source, "delta === null", "watchlist page");
  assertIncludes(source, "delta === 0", "watchlist page");
});

test("signals client exposes the change payload used for delta sorting", () => {
  const source = read("src/lib/api/signals.ts");

  ["score_delta", "previous_signal_date", "previous_score", "getSignalsByStockIds"].forEach(
    (expected) => assertIncludes(source, expected, "signals client"),
  );
});

test("mypage keeps account guard and tab data sources wired", () => {
  const source = read("src/app/mypage/page.tsx");

  [
    'data-page="mypage"',
    'router.replace("/login")',
    '"watchlist"',
    '"journal"',
    '"social"',
    '"profile"',
    "useJournalStore",
    "useWatchlistStore",
    "useSocialStore",
  ].forEach((expected) => assertIncludes(source, expected, "mypage"));

  // 구독(결제 관리) 탭 제거 확인.
  assert.ok(!source.includes('"subscription"'), 'mypage should NOT include "subscription" tab key');
  assert.ok(!source.includes("getMySubscription()"), "mypage should NOT call getMySubscription()");
  assert.ok(!source.includes("paymentHistory()"), "mypage should NOT call paymentHistory()");
});

test("methodology page explains posthoc alignment without recommendation framing", () => {
  const source = read("src/app/methodology/page.tsx");

  [
    'data-page="methodology"',
    "사후정합성",
    "데이터 방향성",
    "소스 간 일치도",
    "확정 대기",
    "표본 부족",
    "미래 결과를 보장하지 않습니다",
    "사용자 판단 보조",
    "저널 기준",
    "전체 발행 신호 기준",
    "방향성별 breakdown",
    "마지막 검증 반영",
    "PosthocAlignmentSummary",
  ].forEach((expected) => assertIncludes(source, expected, "methodology page"));

  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "getPosthocAlignment", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "item.aligned_count", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "item.not_aligned_count", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "정합", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "비정합", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "data.groups", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "data.methodology", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "data.notice", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "검증 기준", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "포함 기준", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "제외 기준", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "item.direction_breakdown", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "latestCheckedAt", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "formatVerificationTimestamp", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "마지막 검증 반영", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "검증 데이터 연결 대기", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "긍정 방향성", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "부정 방향성", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "전체 발행 신호 기준", "methodology summary");
  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "저널 기준", "methodology summary");
  // 방법론은 상단 메뉴에서 마이 안으로 이동 — 마이페이지가 링크를 배선한다.
  assertIncludes(read("src/app/mypage/page.tsx"), 'href="/methodology"', "mypage navigation");
});
