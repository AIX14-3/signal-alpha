import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

test("dashboard shell defines the initial Signal Alpha screen", async () => {
  const page = await readFile(new URL("../src/app/page.tsx", import.meta.url), "utf8");
  const layout = await readFile(new URL("../src/app/layout.tsx", import.meta.url), "utf8");
  const apiClient = await readFile(new URL("../src/lib/apiClient.ts", import.meta.url), "utf8");

  assert.match(page, /Signal Alpha/);
  assert.match(page, /소스 방향성 일치도/);
  assert.match(layout, /AppShell/);
  assert.match(apiClient, /MAIN_API_BASE_URL/);
});
