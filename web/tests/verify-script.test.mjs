import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const EXPECTED_VERIFY_SCRIPT = "npm run lint && npm run build && npm run test && npm run test:render";

test("web package exposes one reproducible verification script", async () => {
  const packageJson = JSON.parse(
    await readFile(new URL("../package.json", import.meta.url), "utf8"),
  );

  assert.equal(packageJson.scripts.verify, EXPECTED_VERIFY_SCRIPT);
});

test("CI and docs use the reproducible web verification flow", async () => {
  const ci = await readFile(new URL("../../.github/workflows/ci.yml", import.meta.url), "utf8");
  const rootReadme = await readFile(new URL("../../README.md", import.meta.url), "utf8");
  const webReadme = await readFile(new URL("../README.md", import.meta.url), "utf8");

  assert.match(ci, /run:\s*npm ci/);
  assert.match(ci, /run:\s*npm run verify/);
  assert.doesNotMatch(ci, /run:\s*npm run lint/);
  assert.doesNotMatch(ci, /run:\s*npm run build/);
  assert.doesNotMatch(ci, /run:\s*npm test/);

  for (const doc of [rootReadme, webReadme]) {
    assert.match(doc, /npm ci/);
    assert.match(doc, /npm run verify/);
  }
});
