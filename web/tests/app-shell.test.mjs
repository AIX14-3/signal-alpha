import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import test from "node:test";

// apiClient 는 도메인별 모듈(lib/api/*.ts)로 분할되고 apiClient.ts 는 배럴 재-export 다.
// 계약 검사는 배럴 + 도메인 모듈 소스를 합쳐서 본다(어느 모듈에 있든 무방).
async function readApiSource() {
  const barrelUrl = new URL("../src/lib/apiClient.ts", import.meta.url);
  const apiDirUrl = new URL("../src/lib/api/", import.meta.url);
  const files = await readdir(apiDirUrl);
  const parts = await Promise.all([
    readFile(barrelUrl, "utf8"),
    ...files
      .filter((name) => name.endsWith(".ts"))
      .map((name) => readFile(new URL(name, apiDirUrl), "utf8")),
  ]);
  return parts.join("\n");
}

// admin 화면은 page.tsx + _components/*.tsx + _lib/*.ts 로 분할됐다.
// UI 계약 검사는 admin 디렉토리 전체 소스를 합쳐서 본다(어느 파일에 있든 무방).
async function readAdminSource() {
  const adminDirUrl = new URL("../src/app/admin/", import.meta.url);
  const entries = await readdir(adminDirUrl, { recursive: true });
  const parts = await Promise.all(
    entries
      .filter((name) => /\.tsx?$/.test(name))
      .map((name) => readFile(new URL(name.replace(/\\/g, "/"), adminDirUrl), "utf8")),
  );
  return parts.join("\n");
}

test("home dashboard wires market-indices/watchlist bands and the two-column layout", async () => {
  const page = await readFile(new URL("../src/app/page.tsx", import.meta.url), "utf8");
  const rightPane = await readFile(new URL("../src/components/HomeRightPane.tsx", import.meta.url), "utf8");
  const liveAnalysis = await readFile(new URL("../src/components/LiveAnalysisSection.tsx", import.meta.url), "utf8");
  const watchlistSection = await readFile(new URL("../src/components/WatchlistSection.tsx", import.meta.url), "utf8");
  const marketIndices = await readFile(new URL("../src/components/MarketIndices.tsx", import.meta.url), "utf8");
  const communityPopular = await readFile(new URL("../src/components/CommunityPopularSection.tsx", import.meta.url), "utf8");
  const headerSearch = await readFile(new URL("../src/components/HeaderStockSearch.tsx", import.meta.url), "utf8");
  const layout = await readFile(new URL("../src/app/layout.tsx", import.meta.url), "utf8");
  const apiClient = await readFile(new URL("../src/lib/apiClient.ts", import.meta.url), "utf8");

  // 홈 대시보드 = 상단 가로 밴드(시장 지수·관심종목) + 좌 뉴스 피드 / 우 2섹션. 인-콘텐츠 메뉴 사이드바는 제거됐다.
  assert.match(page, /MarketIndices/);
  assert.match(page, /WatchlistSection/);
  assert.match(page, /HomeLeftPane/);
  assert.match(page, /HomeRightPane/);
  assert.doesNotMatch(page, /HomeSidebar/);
  // 우 pane 은 실시간 분석 종목 / 커뮤니티 인기순위 2섹션을 조립한다(관심종목은 상단 밴드로 승격).
  assert.match(rightPane, /LiveAnalysisSection/);
  assert.match(rightPane, /CommunityPopularSection/);
  assert.doesNotMatch(rightPane, /WatchlistSection/);
  // 실시간 분석 아코디언: 캔들 차트 + 리포트(useReportStore) + 전체 리포트 링크 배선.
  assert.match(liveAnalysis, /StockCandleChart/);
  assert.match(liveAnalysis, /useReportStore/);
  assert.match(liveAnalysis, /\/report\//);
  // 관심종목 로그인 분기 + 시장 지수 밴드 variant + 커뮤니티 인기순위 소스.
  assert.match(watchlistSection, /로그인 후 관심종목을 등록하세요/);
  assert.match(watchlistSection, /useWatchlistStore/);
  assert.match(marketIndices, /variant === "band"/);
  assert.match(communityPopular, /listPopular/);
  assert.match(headerSearch, /searchStocks/);
  assert.match(layout, /AppShell/);
  assert.match(apiClient, /MAIN_API_BASE_URL/);
});

test("api client exposes the contracted endpoints", async () => {
  const apiClient = await readApiSource();
  const communityApi = await readFile(new URL("../src/lib/api/community.ts", import.meta.url), "utf8");

  for (const fn of [
    "searchStocks",
    "getStockPrices",
    "listWatchlists",
    "getReport",
    "getPosthocAlignment",
    "getMySubscription",
    "adminLogin",
    "adminGetStats",
  ]) {
    assert.match(apiClient, new RegExp(`export async function ${fn}`));
  }

  assert.match(apiClient, /groups: PosthocAlignmentGroup\[\]/);
  assert.match(apiClient, /scope: "journal_based" \| "signal_based"/);
  assert.match(apiClient, /export type PosthocDirectionBreakdownItem/);
  assert.match(apiClient, /direction_breakdown: PosthocDirectionBreakdownItem\[\]/);
  assert.match(communityApi, /next_cursor: string \| null/);
  assert.match(communityApi, /cursor\?: string \| null/);
  assert.match(
    communityApi,
    /export type CommunityComments = \{\s+items: CommunityComment\[\];\s+next_cursor: number \| null;/s,
  );
  assert.match(
    communityApi,
    /listComments\(\s*postId: number,\s*params: \{ cursor\?: number \| null; limit\?: number \}/s,
  );
  assert.doesNotMatch(communityApi, /offset\?: number/);
  assert.match(communityApi, /bookmark_count: number/);
  assert.match(communityApi, /my_reactions: \{ like: boolean; bookmark: boolean \}/);
  assert.match(communityApi, /active: boolean/);
});

test("community detail exposes bookmark and active reaction state", async () => {
  const page = await readFile(new URL("../src/app/community/[postId]/page.tsx", import.meta.url), "utf8");
  const reactionButton = await readFile(new URL("../src/components/community/ReactionButton.tsx", import.meta.url), "utf8");
  const commentList = await readFile(new URL("../src/components/community/CommentList.tsx", import.meta.url), "utf8");

  assert.match(page, /type="bookmark"/);
  assert.match(page, /post\.my_reactions\.like/);
  assert.match(page, /post\.my_reactions\.bookmark/);
  assert.match(page, /bookmark_count/);
  assert.match(reactionButton, /data-flow=\{`community-\$\{type\}`\}/);
  assert.match(reactionButton, /result\.active/);
  assert.match(reactionButton, /result\.bookmark_count/);
  assert.match(commentList, /comment\.my_reactions\.like/);
  assert.match(commentList, /comment\.my_reactions\.bookmark/);
  assert.match(commentList, /comment\.bookmark_count/);
  assert.match(commentList, /type="bookmark"/);
  assert.match(commentList, /nextCursor/);
  assert.match(commentList, /loadMore/);
  assert.match(commentList, /listComments\(postId, \{ limit: PAGE, cursor/);
  assert.match(page, /onCountChange/);
  assert.match(commentList, /onCountChange/);
});

test("community copy uses data direction and evidence framing", async () => {
  const communityPage = await readFile(new URL("../src/app/community/page.tsx", import.meta.url), "utf8");
  const mypage = await readFile(new URL("../src/app/mypage/page.tsx", import.meta.url), "utf8");

  assert.match(communityPage, /데이터 방향성/);
  assert.match(communityPage, /근거/);
  assert.doesNotMatch(communityPage, /투자 판단/);
  assert.match(mypage, /데이터 방향성 기록/);
  assert.match(mypage, /데이터 방향성과 근거/);
  assert.doesNotMatch(mypage, /나의 판단|투자 추이|판단 성향|판단 후/);
});

test("admin UI exposes split schedule rows and schedule run history", async () => {
  const page = await readAdminSource();
  const apiClient = await readApiSource();

  assert.match(apiClient, /export async function adminListScheduleRuns/);
  assert.match(apiClient, /frequency_minutes: number \| null/);
  assert.match(apiClient, /active_from_local: string \| null/);
  assert.match(apiClient, /active_until_local: string \| null/);
  assert.match(apiClient, /report_limit: number \| null/);
  assert.match(apiClient, /alternative_collect_enabled: boolean \| null/);
  assert.match(apiClient, /backpressure_max_waiting: number \| null/);
  assert.match(apiClient, /health_status: string \| null/);
  assert.match(apiClient, /health_detail: string \| null/);
  assert.match(apiClient, /export async function adminDryRunSchedule/);
  assert.match(page, /adminListScheduleRuns/);
  assert.match(page, /adminDryRunSchedule/);
  assert.match(page, /savePolicy/);
  assert.match(page, /dryRunSchedule/);
  assert.match(page, /schedules\.map/);
  assert.match(page, /formatScheduleRunDecision/);
  assert.match(page, /formatScheduleRunTargetResult/);
  assert.match(page, /formatScheduleHealth/);
  assert.match(page, /getScheduleRunWarning/);
  assert.match(page, /validateScheduleDraft/);
  assert.match(page, /decision/);
  assert.match(page, /queue-backlog/);
  assert.match(page, /failed_waiting/);
  assert.match(page, /delayed/);
  assert.match(page, /\ubc18\ubcf5 \ubcf4\ub958\/\uc2e4\ud328/);
  assert.match(page, /\uc218\uc9d1 \ub300\uc0c1\uc744 \ucd5c\uc18c 1\uac1c \uc120\ud0dd/);
  assert.match(page, /frequency_minutes/);
  assert.match(page, /active_from_local/);
  assert.match(page, /active_until_local/);
  assert.match(page, /report_limit/);
  assert.match(page, /alternative_collect_enabled/);
  assert.match(page, /backpressure_max_waiting/);
  assert.match(page, /\\uc2e4\\ud589 \\uc815\\ucc45/);
  assert.match(page, /\\ubbf8\\ub9ac \\ud310\\ub2e8/);
  assert.match(page, /"price", "dart", "report", "alternative"/);
  assert.match(apiClient, /export async function adminGetQueueOverview/);
  assert.match(apiClient, /export async function adminSweepStaleQueue/);
  assert.match(apiClient, /export async function adminRetryQueueTask/);
  assert.match(apiClient, /export async function adminReplayDeadLetters/);
  assert.match(apiClient, /export type AdminCommunityModerationItem/);
  assert.match(apiClient, /export async function adminListCommunityModeration/);
  assert.match(
    apiClient,
    /adminListCommunityModeration\(\s*params: \{ target_type\?: AdminCommunityModerationTarget; limit\?: number; cursor\?: string \| null \}/s,
  );
  assert.match(
    apiClient,
    /Promise<\{ items: AdminCommunityModerationItem\[\]; target_type: AdminCommunityModerationTarget; next_cursor: string \| null \}>/s,
  );
  assert.match(apiClient, /export async function adminRestoreCommunityPost/);
  assert.match(apiClient, /export async function adminDeleteCommunityPost/);
  assert.match(apiClient, /export async function adminRestoreCommunityComment/);
  assert.match(apiClient, /export async function adminDeleteCommunityComment/);
  assert.match(page, /QueueOpsCard/);
  assert.match(page, /CommunityModerationCard/);
  assert.match(page, /adminListCommunityModeration/);
  assert.match(page, /nextCursor/);
  assert.match(page, /loadMore/);
  assert.match(page, /adminListCommunityModeration\(\{ target_type: "all", limit: PAGE, cursor/);
  assert.match(page, /adminRestoreCommunityPost/);
  assert.match(page, /adminDeleteCommunityPost/);
  assert.match(page, /adminRestoreCommunityComment/);
  assert.match(page, /adminDeleteCommunityComment/);
  assert.match(page, /moderation_review/);
  assert.match(page, /adminGetQueueOverview/);
  assert.match(page, /adminSweepStaleQueue/);
  assert.match(page, /adminRetryQueueTask/);
  assert.match(page, /adminReplayDeadLetters/);
  assert.match(page, /\\uc6b4\\uc601 \\uc774\\ubca4\\ud2b8/);
  assert.match(page, /dead_letter_pending/);
});

test("home surfaces company logos with an initials fallback across watchlist/feed/analysis", async () => {
  const stockLogo = await readFile(new URL("../src/components/StockLogo.tsx", import.meta.url), "utf8");
  const stocksApi = await readFile(new URL("../src/lib/api/stocks.ts", import.meta.url), "utf8");
  const watchlistSection = await readFile(new URL("../src/components/WatchlistSection.tsx", import.meta.url), "utf8");
  const homeLeftPane = await readFile(new URL("../src/components/HomeLeftPane.tsx", import.meta.url), "utf8");
  const liveAnalysis = await readFile(new URL("../src/components/LiveAnalysisSection.tsx", import.meta.url), "utf8");

  // 로고 URL 은 절대 URL(<img src>)로 백엔드 발행 사본을 부른다.
  assert.match(stocksApi, /export function stockLogoUrl/);
  assert.match(stocksApi, /\/api\/stocks\/.+\/logo/);
  // StockLogo 는 <img> + onError 폴백(미발행/실패 시 이니셜)을 그린다.
  assert.match(stockLogo, /stockLogoUrl/);
  assert.match(stockLogo, /onError/);
  // 홈 3곳(관심종목·뉴스 피드·실시간 분석)이 로고를 배선한다.
  assert.match(watchlistSection, /StockLogo/);
  assert.match(homeLeftPane, /StockLogo/);
  assert.match(liveAnalysis, /StockLogo/);
});
