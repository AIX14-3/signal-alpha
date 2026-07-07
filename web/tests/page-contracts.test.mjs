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

test("report page keeps ticker loading, subscription lock redirect, and watchlist wired", () => {
  const source = read("src/app/report/[ticker]/page.tsx");

  [
    'data-page="report"',
    "useParams<{ ticker: string }>()",
    "useReportStore",
    "load(ticker)",
    'router.push(isMember ? "/pricing" : loginHref)',
    "WatchlistButton",
    "SOURCE_ORDER",
    "PREDICTION_RATE_ORDER",
  ].forEach((expected) => assertIncludes(source, expected, "report page"));
});

test("mypage keeps account guard and tab data sources wired", () => {
  const source = read("src/app/mypage/page.tsx");

  [
    'data-page="mypage"',
    'router.replace("/login")',
    '"watchlist"',
    '"subscription"',
    '"journal"',
    '"social"',
    '"profile"',
    "getMySubscription()",
    "paymentHistory()",
    "useJournalStore",
    "useWatchlistStore",
    "useSocialStore",
  ].forEach((expected) => assertIncludes(source, expected, "mypage"));
});

test("methodology page explains posthoc alignment without recommendation framing", () => {
  const source = read("src/app/methodology/page.tsx");
  const shell = read("src/components/AppShell.tsx");

  [
    'data-page="methodology"',
    "사후정합성",
    "데이터 방향성",
    "소스 간 일치도",
    "확정 대기",
    "표본 부족",
    "미래 결과를 보장하지 않습니다",
    "사용자 판단 보조",
    "PosthocAlignmentSummary",
  ].forEach((expected) => assertIncludes(source, expected, "methodology page"));

  assertIncludes(read("src/components/PosthocAlignmentSummary.tsx"), "getPosthocAlignment", "methodology summary");
  assertIncludes(shell, 'href: "/methodology"', "app shell navigation");
});
