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

test("report page exposes stable locked report and source flow anchors", () => {
  const source = read("src/app/report/[ticker]/page.tsx");

  [
    'data-flow="unlock-report-pricing"',
    'data-flow="unlock-report-login"',
    'data-flow="unlock-source"',
    "data-source={sourceKey}",
    'data-flow="unlock-prediction-rate"',
    "router.push(isMember ? \"/pricing\" : loginHref)",
  ].forEach((expected) => assertIncludes(source, expected, "report page"));
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
    "JournalChartPanel",
    "JournalTimelinePanel",
  ].forEach((expected) => assertIncludes(source, expected, "mypage"));
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

test("report page exposes journal save entry point for unlocked reports", () => {
  const source = read("src/app/report/[ticker]/page.tsx");

  [
    'data-flow="journal-save"',
    "useJournalStore",
    "final_signal_id: finalSignalId",
  ].forEach((expected) => assertIncludes(source, expected, "report page"));
});
