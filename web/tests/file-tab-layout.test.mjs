import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

const ROOT = process.cwd();
const COMPONENTS = join(ROOT, "src/components");

function tabbedComponents() {
  return readdirSync(COMPONENTS)
    .filter((f) => f.endsWith(".tsx"))
    .map((f) => [f, readFileSync(join(COMPONENTS, f), "utf8")])
    .filter(([, source]) => source.includes("file-tab"));
}

test("every card wearing a file-tab reserves room for it", () => {
  const tabbed = tabbedComponents();
  assert.ok(tabbed.length > 0, "file-tab 을 쓰는 컴포넌트가 있어야 한다");

  for (const [file, source] of tabbed) {
    // 탭은 카드 위로 13px 솟고 나머지는 카드 안쪽을 파고든다. 기본 p-5(20px) 만으로는
    // 본문 첫 줄이 탭에 덮인다 — 파고든 만큼 상단 여백(pt-7 = 28px)을 줘야 한다.
    assert.match(
      source,
      /className="glass relative mt-6 p-5 pt-7"/,
      `${file}: file-tab 카드는 'p-5 pt-7' 이어야 한다(본문이 탭에 덮임)`,
    );
  }
});

test("file-tab pins its own line-height so body leading cannot grow it", () => {
  const css = readFileSync(join(ROOT, "src/app/globals.css"), "utf8");
  const rule = css.slice(css.indexOf(".file-tab {"));
  const body = rule.slice(0, rule.indexOf("}"));

  assert.match(body, /line-height:\s*1\.2;/, "body 의 line-height(1.55) 상속을 끊어야 한다");
  assert.match(body, /font-size:\s*12\.5px;/, "높이 계산의 전제");
  assert.match(body, /padding:\s*5px 13px;/, "높이 계산의 전제");
});

test("the tab clears the card's first line of text", () => {
  // 탭 높이 = border-top 1 + padding 5 + 본문줄(아이콘 13px 과 12.5*1.2=15px 중 큰 값) + padding 5
  const tabHeight = 1 + 5 + Math.max(13, 12.5 * 1.2) + 5; // 26px
  const overhang = 13; // .file-tab 의 -top-[13px]
  const intrusion = tabHeight - overhang; // 카드 안쪽으로 파고드는 높이
  const paddingTop = 28; // pt-7

  assert.ok(
    paddingTop > intrusion,
    `상단 여백(${paddingTop}px)이 탭 침범(${intrusion}px)보다 커야 본문이 안 덮인다`,
  );
});
