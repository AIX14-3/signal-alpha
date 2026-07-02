import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("home page wires the search hero and report links", async () => {
  const page = await readFile(new URL("../src/app/page.tsx", import.meta.url), "utf8");
  const hero = await readFile(new URL("../src/components/SearchHero.tsx", import.meta.url), "utf8");
  const layout = await readFile(new URL("../src/app/layout.tsx", import.meta.url), "utf8");
  const apiClient = await readFile(new URL("../src/lib/apiClient.ts", import.meta.url), "utf8");

  assert.match(page, /SearchHero/);
  assert.match(hero, /\/report\//);
  assert.match(layout, /AppShell/);
  assert.match(apiClient, /MAIN_API_BASE_URL/);
});

test("api client exposes the contracted endpoints", async () => {
  const apiClient = await readFile(new URL("../src/lib/apiClient.ts", import.meta.url), "utf8");

  for (const fn of [
    "searchStocks",
    "listWatchlists",
    "getReport",
    "getMySubscription",
    "adminLogin",
    "adminGetStats",
  ]) {
    assert.match(apiClient, new RegExp(`export async function ${fn}`));
  }
});

test("admin UI exposes split schedule rows and schedule run history", async () => {
  const page = await readFile(new URL("../src/app/admin/page.tsx", import.meta.url), "utf8");
  const apiClient = await readFile(new URL("../src/lib/apiClient.ts", import.meta.url), "utf8");

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
