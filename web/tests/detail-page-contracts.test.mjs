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

test("source detail page keeps source validation, lock handling, and evidence sections wired", () => {
  const source = read("src/app/report/[ticker]/[source]/page.tsx");

  [
    'data-page="source-detail"',
    "useParams<{ ticker: string; source: string }>()",
    'const VALID: SourceKey[] = ["price", "dart", "hiring", "datalab", "patent", "report"]',
    "getSourceDetail(ticker, source)",
    "err instanceof ApiError && (err.status === 401 || err.status === 402)",
    'setState("locked")',
    "SOURCE_META[source]",
    "directionLabel(detail.direction)",
    "detail.valuation",
    "detail.narrative_points",
    "detail.items.map",
    "detail.notice",
    'href={`/report/${encodeURIComponent(ticker)}`}',
  ].forEach((expected) => assertIncludes(source, expected, "source detail page"));
});

test("social callback page keeps oauth state checks and login/link redirects wired", () => {
  const source = read("src/app/auth/callback/[provider]/page.tsx");

  [
    'data-page="social-callback"',
    "useParams<{ provider: string }>()",
    "readOAuthState()",
    "clearOAuthState()",
    'saved?.intent === "login" ? "/login" : "/mypage"',
    "showToast(msg, \"error\")",
    "setTimeout(() => router.replace(fallback), 1500)",
    "callbackUri(provider)",
    'saved.intent === "link"',
    "linkSocial(provider, body)",
    "socialLogin(provider, body)",
    "setUserTokens(res.access_token)",
    "await hydrate()",
    'router.replace(saved.returnTo || "/mypage")',
    'router.replace("/mypage")',
  ].forEach((expected) => assertIncludes(source, expected, "social callback page"));
});
