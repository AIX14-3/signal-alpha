import assert from "node:assert/strict";
import { readdir, readFile } from "node:fs/promises";
import { join, relative } from "node:path";
import test from "node:test";
import { fileURLToPath } from "node:url";

const SRC_DIR = fileURLToPath(new URL("../src", import.meta.url));
const CHECKED_EXTENSIONS = new Set([".ts", ".tsx"]);

const FORBIDDEN_COPY_PATTERNS = [
  { name: "매수 우위", pattern: /매수\s*우위/ },
  { name: "매도 우위", pattern: /매도\s*우위/ },
  { name: "추천 플랜 라벨", pattern: /유료\s*구독\s*·\s*추천/ },
  { name: "매수 추천", pattern: /매수\s*추천(?!이\s*아니라)/ },
  { name: "매도 추천", pattern: /매도\s*추천(?!이\s*아니라)/ },
  { name: "추천 종목", pattern: /추천\s*종목/ },
  { name: "목표 수익", pattern: /목표\s*수익/ },
  { name: "투자 타이밍", pattern: /투자\s*타이밍/ },
  { name: "즉시 매매 권유", pattern: /지금\s*(?:사|팔|매수|매도)(?:세요|하)/ },
  { name: "수익 보장", pattern: /수익\s*보장(?!\s*아님)/ },
];

async function listSourceFiles(dir) {
  const entries = await readdir(dir, { withFileTypes: true });
  const files = await Promise.all(
    entries.map(async (entry) => {
      const path = join(dir, entry.name);
      if (entry.isDirectory()) return listSourceFiles(path);
      if ([...CHECKED_EXTENSIONS].some((extension) => path.endsWith(extension))) return [path];
      return [];
    }),
  );
  return files.flat();
}

test("user-facing source avoids investment recommendation copy", async () => {
  const files = await listSourceFiles(SRC_DIR);
  const violations = [];

  for (const file of files) {
    const source = await readFile(file, "utf8");
    for (const { name, pattern } of FORBIDDEN_COPY_PATTERNS) {
      if (pattern.test(source)) {
        violations.push(`${relative(SRC_DIR, file)}: ${name}`);
      }
    }
  }

  assert.deepEqual(violations, []);
});
