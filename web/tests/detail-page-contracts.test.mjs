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

test("source detail page keeps source validation and delegates body to the shared component", () => {
  const source = read("src/app/report/[ticker]/[source]/page.tsx");

  [
    'data-page="source-detail"',
    "useParams<{ ticker: string; source: string }>()",
    'const VALID: SourceKey[] = ["price", "dart", "hiring", "datalab", "patent", "report"]',
    "getSourceDetail(ticker, source)",
    "SOURCE_META[source]",
    // 본문은 슬라이드오버와 공유한다(중복 구현 금지).
    "<SourceDetailBody detail={detail} source={source} />",
    'href={`/report/${encodeURIComponent(ticker)}`}',
  ].forEach((expected) => assertIncludes(source, expected, "source detail page"));

  // 비회원 블라인드 제거 — 소스 상세는 전체 공개라 잠금 상태 처리가 없어야 한다.
  ['setState("locked")', "err.status === 401 || err.status === 402"].forEach((removed) =>
    assert.ok(!source.includes(removed), `source detail page should NOT include ${JSON.stringify(removed)}`),
  );
});

test("shared source detail body renders verdict, narrative, evidence links and references", () => {
  const source = read("src/components/SourceDetailBody.tsx");

  [
    "data-source-detail={source}",
    "directionLabel(detail.direction)",
    "detail.narrative_points",
    "detail.valuation",
    "detail.patent",
    // 특허는 공개일 기준 경과일("공개 N일 전")을 함께 보여준다.
    "relativeDayLabel(p.publication_date)",
    // 출원추이는 원본이 아니라 "완성된 연도"만 그린다. 수집된 특허는 이미 공개된 것뿐이라
    // 최근 출원연도는 미달집계되고, 그대로 그리면 R&D 급감으로 오독된다.
    "completeFilingYears(detail.patent?.filing_trend ?? [])",
    "<FilingTrendChart data={filingTrend} />",
    // 채용은 게시일 경과("N일 전")와 마감 상태를 보여준다. 상시채용은 만료가 아니다.
    "detail.hiring",
    "relativeDayLabel(p.posting_date)",
    "isExpired(p.closing_date)",
    "Boolean(p.is_always_open)",
    "상시채용",
    // 실제 근거 문서는 evidence_url 이 있는 항목만 원본으로 노출한다.
    "detail.items.filter((it) => safeHttpUrl(it.evidence_url))",
    // 원본 문서가 없는 소스는 상류 출처를 밝히는 참고 링크로 대체한다.
    "referenceLinks(",
    "detail.notice",
  ].forEach((expected) => assertIncludes(source, expected, "source detail body"));
});

test("filing trend excludes the trailing years that publication lag leaves undercounted", () => {
  const source = read("src/lib/format.ts");

  [
    "export const FILING_TREND_INCOMPLETE_YEARS = 2",
    "const cutoff = todayKST().getFullYear() - FILING_TREND_INCOMPLETE_YEARS",
    "return trend.filter((d) => d.year <= cutoff)",
  ].forEach((expected) => assertIncludes(source, expected, "completeFilingYears"));
});

test("source detail opens as a centered document dialog whose open state lives in the URL", () => {
  const panel = read("src/components/SourceDetailPanel.tsx");
  [
    'data-panel="source-detail"',
    'role="dialog"',
    'aria-modal="true"',
    "getSourceDetail(ticker, source)",
    // Esc 로 닫히고 배경 스크롤을 잠근다.
    'e.key === "Escape"',
    'document.body.style.overflow = "hidden"',
    "<SourceDetailBody detail={detail} source={source} />",
    // 서류철에서 꺼낸 종이 = 화면 중앙의 보고서(우측 슬라이드오버 아님).
    "grid place-items-center",
    "doc-sheet",
    "doc-body",
    // 닫힘은 퇴장 애니메이션 후 언마운트한다(서류가 서류철로 돌아가는 동작과 연결).
    "PANEL_EXIT_MS",
  ].forEach((expected) => assertIncludes(panel, expected, "source detail panel"));

  const report = read("src/app/report/[ticker]/page.tsx");
  [
    // ?source=dart 가 열림 상태의 단일 진실원 — 뒤로가기로 닫히고 새로고침에도 유지된다.
    'searchParams.get("source")',
    "router.push(`${pathname}?source=${source}`, { scroll: false })",
    "<SourceDetailPanel ticker={ticker} source={openSource} onClose={closePanel} />",
    // useSearchParams 는 Suspense 경계가 필요하다(Next 15 정적 프리렌더 바일아웃).
    "<Suspense",
  ].forEach((expected) => assertIncludes(report, expected, "report page"));

  // 상세는 더 이상 새 페이지로 이동하지 않는다(카드가 링크가 아니라 버튼).
  assert.ok(
    !report.includes("href={`/report/${encodeURIComponent(ticker)}/${sourceKey}`}"),
    "report page should NOT navigate to the standalone source page from source cards",
  );
});

test("source cards are folders with uniform height and clamped summaries", () => {
  const report = read("src/app/report/[ticker]/page.tsx");
  [
    // 서류철 구조: 뒷표지 + 서류 3장 + 앞표지.
    "folder-back",
    "folder-paper folder-paper-1",
    "folder-paper folder-paper-2",
    "folder-paper folder-paper-3",
    // 열림 상태가 카드에 전달돼 서류가 빠져나가는 애니메이션을 탄다.
    'open ? "is-open" : ""',
    // 표지 높이 통일: 행 높이를 채우고 요약은 3줄에서 말줄임(뒷표지·서류가 삐져나오지 않게).
    "flex h-full w-full flex-col",
    "line-clamp-3",
    // CTA 는 표지 맨 아래 고정(mt-auto) — 요약 길이가 달라도 높이가 어긋나지 않는다.
    "상세 서류 보기",
    "mt-auto pt-3",
  ].forEach((expected) => assertIncludes(report, expected, "report page source cards"));

  // CTA 에 화살표는 넣지 않는다.
  assert.ok(
    !report.includes("상세 서류 보기 →"),
    "source card CTA should NOT include a trailing arrow",
  );
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
