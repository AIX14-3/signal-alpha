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

test("report page keeps ticker loading, quota, lock redirect, and watchlist wired", () => {
  const source = read("src/app/report/[ticker]/page.tsx");

  [
    'data-page="report"',
    "useParams<{ ticker: string }>()",
    "useReportStore",
    "load(ticker)",
    "issue(ticker)",
    "loadQuota()",
    "ApiError",
    'router.push("/pricing")',
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
    "listJournals({ limit: 50 })",
    "useWatchlistStore",
    "useSocialStore",
  ].forEach((expected) => assertIncludes(source, expected, "mypage"));
});
