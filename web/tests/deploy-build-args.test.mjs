import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

// web/ 에서 실행되므로 레포 루트는 한 단계 위.
const REPO = join(process.cwd(), "..");

test("the deploy workflow passes every NEXT_PUBLIC_* arg the web image expects", () => {
  // NEXT_PUBLIC_* 은 빌드타임에 번들로 인라인된다. 워크플로가 하나를 빠뜨리면 그 값은 빈 문자열이
  // 되고, 해당 기능(예: 카카오 로그인)이 배포본에서만 조용히 죽는다 — 빌드는 성공한다.
  const dockerfile = readFileSync(join(REPO, "web/Dockerfile"), "utf8");
  const workflow = readFileSync(join(REPO, ".github/workflows/deploy.yml"), "utf8");

  const required = [...dockerfile.matchAll(/^ARG (NEXT_PUBLIC_[A-Z0-9_]+)/gm)].map((m) => m[1]);
  assert.ok(required.length > 0, "web/Dockerfile 에 NEXT_PUBLIC_* ARG 가 있어야 한다");

  const passed = new Set([...workflow.matchAll(/^\s+(NEXT_PUBLIC_[A-Z0-9_]+)=/gm)].map((m) => m[1]));

  const missing = required.filter((name) => !passed.has(name));
  assert.deepEqual(missing, [], `deploy.yml 이 안 넘기는 build-arg: ${missing.join(", ")}`);
});
