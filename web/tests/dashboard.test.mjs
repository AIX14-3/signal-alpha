import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const read = (rel) => readFile(new URL(`../${rel}`, import.meta.url), "utf8");

test("root layout defines the Signal Alpha shell in Korean", async () => {
  const layout = await read("app/layout.tsx");

  assert.match(layout, /Signal α/);
  assert.match(layout, /lang="ko"/);
  assert.match(layout, /export const metadata/);
});

test("dashboard route group mounts the DashboardShell", async () => {
  const layout = await read("app/(dashboard)/layout.tsx");

  assert.match(layout, /DashboardShell/);
});
