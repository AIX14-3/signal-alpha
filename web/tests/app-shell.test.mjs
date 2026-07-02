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
  assert.match(page, /adminListScheduleRuns/);
  assert.match(page, /schedules\.map/);
  assert.match(page, /formatScheduleRunDecision/);
  assert.match(page, /formatScheduleRunTargetResult/);
  assert.match(page, /decision/);
  assert.match(page, /queue-backlog/);
  assert.match(page, /frequency_minutes/);
  assert.match(page, /active_from_local/);
  assert.match(page, /active_until_local/);
  assert.match(page, /"price", "dart", "report", "alternative"/);
});
