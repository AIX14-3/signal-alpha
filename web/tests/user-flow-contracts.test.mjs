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

test("auth form exposes stable login and signup flow anchors", () => {
  const source = read("src/components/AuthForm.tsx");

  [
    'data-page={isSignup ? "signup" : "login"}',
    'role="alert"',
    'data-flow={isSignup ? "identity-signup" : "identity-login"}',
    'data-flow="social-login"',
    "data-provider={s.key}",
    "getReturnTo()",
    'to.startsWith("/")',
    // 이미 로그인된(sa_refresh 복원) 사용자는 로그인/가입 폼 대신 목적지로 리다이렉트.
    'if (status === "authenticated") router.replace(getReturnTo())',
  ].forEach((expected) => assertIncludes(source, expected, "auth form"));
});

test("pricing page exposes stable subscription flow anchors", () => {
  const source = read("src/app/pricing/page.tsx");

  [
    'data-flow="free-plan-cta"',
    'data-flow="billing-monthly"',
    'data-flow="billing-yearly"',
    'data-flow="manage-subscription"',
    'data-flow="start-subscription"',
    'router.push("/login")',
    'router.push("/mypage")',
  ].forEach((expected) => assertIncludes(source, expected, "pricing page"));
});

test("report page renders the full report with no non-member blind gating", () => {
  const source = read("src/app/report/[ticker]/page.tsx");

  // 전체 공개 — 종합 점수·요약·방법론 링크가 항상 렌더된다.
  [
    'data-page="report"',
    "종합 점수",
    "report.summary",
    'data-flow="methodology-link"',
  ].forEach((expected) => assertIncludes(source, expected, "report page"));

  // 비회원 블라인드 게이팅 잔재가 없어야 한다(잠금 CTA·access 분기 제거 확인).
  [
    'data-flow="unlock-report-pricing"',
    'data-flow="unlock-report-login"',
    'data-flow="unlock-source"',
    'data-flow="unlock-prediction-rate"',
    "report.access",
  ].forEach((removed) =>
    assert.ok(!source.includes(removed), `report page should NOT include ${JSON.stringify(removed)}`),
  );
});

test("mypage exposes stable tab panels and subscription action anchors", () => {
  const source = read("src/app/mypage/page.tsx");

  [
    "data-tab={key}",
    'data-panel="watchlist"',
    'data-panel="subscription"',
    'data-panel="journal"',
    'data-panel="social"',
    'data-panel="profile"',
    'data-flow="subscription-resume"',
    'data-flow="subscription-renew"',
    'data-flow="subscription-cancel"',
    'data-flow="subscription-start"',
    'data-flow="subscription-refund"',
    'data-flow="profile-save"',
    'data-flow="profile-withdraw"',
    'data-flow="journal-subscribe"',
    'data-flow="journal-edit"',
    'data-flow="journal-delete"',
    'data-flow="journal-timeline"',
    'data-flow="journal-filter-view"',
    'data-flow="journal-filter-tag"',
    'data-flow="journal-sort"',
    'data-flow="journal-signal-compare"',
    'data-flow="journal-summary"',
    'data-flow="journal-retro-alert"',
    'data-flow="journal-retro-banner"',
    'data-flow="journal-card-retro"',
    "JournalChartPanel",
    "JournalTimelinePanel",
  ].forEach((expected) => assertIncludes(source, expected, "mypage"));
});

test("report page surfaces existing journal history before saving again", () => {
  const source = read("src/app/report/[ticker]/page.tsx");

  [
    'data-flow="journal-history"',
    "listJournals({ stock_code: stockCode })",
  ].forEach((expected) => assertIncludes(source, expected, "report journal history"));
});

test("journal chart panel is wired to the chart API with base reference", () => {
  const source = read("src/components/JournalChart.tsx");

  [
    "getJournalChart",
    "getJournalTimeline",
    'data-flow="journal-chart"',
    'data-flow="journal-retrospective"',
    "change_pct_since_created",
    "base_trade_date",
  ].forEach((expected) => assertIncludes(source, expected, "journal chart"));
});

test("report page exposes journal save entry point for subscribed reports", () => {
  const source = read("src/app/report/[ticker]/page.tsx");

  [
    'data-flow="journal-save"',
    "useJournalStore",
    "final_signal_id: finalSignalId",
  ].forEach((expected) => assertIncludes(source, expected, "report page"));
});
