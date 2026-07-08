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

test("home page wires the 2-pane panes and report links", async () => {
  const page = await readFile(new URL("../src/app/page.tsx", import.meta.url), "utf8");
  const leftPane = await readFile(new URL("../src/components/HomeLeftPane.tsx", import.meta.url), "utf8");
  const rightPane = await readFile(new URL("../src/components/HomeRightPane.tsx", import.meta.url), "utf8");
  const layout = await readFile(new URL("../src/app/layout.tsx", import.meta.url), "utf8");
  const apiClient = await readFile(new URL("../src/lib/apiClient.ts", import.meta.url), "utf8");

  // 홈은 좌/우 pane 을 조립하고, 검색은 좌 pane, 전체 리포트 링크는 우 pane 에 있다.
  assert.match(page, /HomeLeftPane/);
  assert.match(page, /HomeRightPane/);
  assert.match(leftPane, /searchStocks/);
  assert.match(rightPane, /\/report\//);
  assert.match(layout, /AppShell/);
  assert.match(apiClient, /MAIN_API_BASE_URL/);
});

test("api client exposes the contracted endpoints", async () => {
  const apiClient = await readApiSource();
  const communityApi = await readFile(new URL("../src/lib/api/community.ts", import.meta.url), "utf8");

  for (const fn of [
    "searchStocks",
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
  assert.doesNotMatch(communityApi, /offset\?: number/);
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
  assert.match(page, /QueueOpsCard/);
  assert.match(page, /adminGetQueueOverview/);
  assert.match(page, /adminSweepStaleQueue/);
  assert.match(page, /adminRetryQueueTask/);
  assert.match(page, /adminReplayDeadLetters/);
  assert.match(page, /\\uc6b4\\uc601 \\uc774\\ubca4\\ud2b8/);
  assert.match(page, /dead_letter_pending/);
});
