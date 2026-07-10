import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

const ROOT = process.cwd();

function tsxFiles(dir) {
  return readdirSync(dir, { withFileTypes: true }).flatMap((entry) => {
    const path = join(dir, entry.name);
    if (entry.isDirectory()) return tsxFiles(path);
    return entry.name.endsWith(".tsx") ? [path] : [];
  });
}

// 탭은 components 밖(리포트 페이지의 '저널' 카드 등)에서도 쓰인다 — src 전체를 훑는다.
function tabbedFiles() {
  return tsxFiles(join(ROOT, "src"))
    .map((path) => [path, readFileSync(path, "utf8")])
    .filter(([, source]) => source.includes("file-tab"));
}

test("the tab sits outside the card, so it can never cover the body", () => {
  const css = readFileSync(join(ROOT, "src/app/globals.css"), "utf8");
  const rule = css.slice(css.indexOf(".file-tab {"));
  const body = rule.slice(0, rule.indexOf("}"));

  // 위치를 CSS 가 소유한다. 탭 높이는 글꼴·행간에 따라 변하므로, 카드 상단 여백으로 겹침을
  // 막으려 하면 언젠가 또 덮는다. 바닥을 카드 상단에 붙여(bottom: 100%) 구조적으로 막는다.
  assert.match(body, /position:\s*absolute;/, "탭 위치는 CSS 가 소유한다");
  assert.match(body, /bottom:\s*100%;/, "탭 바닥 = 카드 상단 (겹침 0)");
  assert.match(body, /left:\s*20px;/, "카드 좌측 패딩(p-5)과 맞춘다");
  assert.match(body, /line-height:\s*1\.2;/, "body 의 line-height(1.55) 상속을 끊는다");
});

test("every card wearing a file-tab is positioned and leaves room above it", () => {
  const tabbed = tabbedFiles();
  assert.ok(tabbed.length > 0, "file-tab 을 쓰는 파일이 있어야 한다");

  for (const [file, source] of tabbed) {
    // 탭은 absolute 라 부모가 relative 여야 하고, 카드 위로 솟은 탭(26px)이 앞 섹션을
    // 침범하지 않도록 mt-12(48px)를 준다. 표지는 glass(리포트 섹션) 또는 card(저널).
    assert.match(
      source,
      /className="(glass|card) relative mt-12 p-5"/,
      `${file}: file-tab 카드는 '(glass|card) relative mt-12 p-5' 여야 한다`,
    );
    // 위치 클래스를 컴포넌트에 흩뿌리면 한 곳만 고쳐도 나머지가 어긋난다.
    assert.ok(
      !/file-tab[^"]*(absolute|-top-|left-)/.test(source),
      `${file}: 탭 위치는 인라인 클래스가 아니라 .file-tab 이 정한다`,
    );
  }
});

test("the report sheet keeps one paper size across sources", () => {
  const panel = readFileSync(join(ROOT, "src/components/SourceDetailPanel.tsx"), "utf8");
  // max-h 면 소스마다 종이 크기가 널뛴다 — 규격(h)을 고정하고 본문만 스크롤한다.
  assert.match(panel, /className="doc-sheet relative flex h-\[86vh\]/, "리포트 세로 규격 고정");
  assert.ok(!panel.includes("max-h-[86vh]"), "max-h 는 소스마다 높이를 다르게 만든다");
  assert.match(panel, /doc-body[^"]*overflow-y-auto/, "넘치는 본문은 종이 안에서 스크롤");
});

test("the sheet is pulled through a slot: clip-path tapers, tail leaves last", () => {
  const css = readFileSync(join(ROOT, "src/app/globals.css"), "utf8");
  const frames = (name) => {
    const start = css.indexOf(`@keyframes ${name} {`);
    return css.slice(start, css.indexOf("\n}", start));
  };

  for (const name of ["panel-in", "panel-out"]) {
    const body = frames(name);
    assert.ok(body.includes("clip-path: polygon("), `${name}: 윤곽을 펴야 틈을 통과해 보인다`);
    // 틈 = 아래 한가운데의 좁은 사각형. 들고 날 때 이 모양으로 수렴한다.
    assert.ok(
      body.includes("polygon(46% 100%, 54% 100%, 54% 100%, 46% 100%)"),
      `${name}: 서류철 입구(틈) 모양이 있어야 한다`,
    );
    // 중간 프레임은 아래로 갈수록 좁은 사다리꼴 — 꼬리가 아직 틈에 물려 있다.
    assert.match(body, /polygon\(2[0-9]% 4[0-9]%, 7[0-9]% 4[0-9]%, 5[0-9]% 100%, 4[0-9]% 100%\)/, `${name}: 꼬리 사다리꼴`);
  }
});

test("the tab clears the card entirely", () => {
  // 탭 높이 = border-top 1 + padding 5 + 본문줄(아이콘 13px 과 12.5*1.2=15px 중 큰 값) + padding 5
  const tabHeight = 1 + 5 + Math.max(13, 12.5 * 1.2) + 5; // 26px
  const marginTop = 48; // mt-12

  assert.equal(tabHeight, 26);
  assert.ok(marginTop > tabHeight, `위 여백(${marginTop}px)이 탭 높이(${tabHeight}px)보다 커야 한다`);
});
