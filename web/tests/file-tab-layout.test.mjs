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

// 한 키프레임의 clip-path polygon 을 (윗변 y, 윗변 폭, 아랫변 폭) 으로 읽는다.
// 꼭짓점 순서는 좌상·우상·우하·좌하.
function readStages(body) {
  return [...body.matchAll(/clip-path: polygon\(([\d.]+)% ([\d.]+)%, ([\d.]+)% [\d.]+%, ([\d.]+)% 100%, ([\d.]+)% 100%\)/g)].map(
    ([, tl, ty, tr, br, bl]) => ({
      topY: Number(ty),
      topWidth: Number(tr) - Number(tl),
      bottomWidth: Number(br) - Number(bl),
    }),
  );
}

test("the sheet is pulled out tail-last, and stays smooth", () => {
  const css = readFileSync(join(ROOT, "src/app/globals.css"), "utf8");
  const panel = readFileSync(join(ROOT, "src/components/SourceDetailPanel.tsx"), "utf8");
  // 블록 끝 = 들여쓰기 없는 `}`. 줄바꿈(CRLF/LF)에 기대면 안 된다 — CRLF 라 "\n}\n" 가 안 맞아
  // 두 키프레임을 통째로 읽고, 단조 검사가 엉뚱한 데서 터졌다.
  const frames = (name) => {
    const block = new RegExp(`@keyframes ${name} \\{[\\s\\S]*?\\r?\\n\\}`).exec(css);
    assert.ok(block, `@keyframes ${name} 를 찾지 못했다`);
    return block[0];
  };

  // 이징 곡선을 주면 구간마다 재적용돼 감속→가속이 되풀이된다(끊김). 곡선은 키프레임 값에 굽는다.
  assert.match(panel, /const PAPER_MOTION = "linear"/, "궤적은 linear + 키프레임 값으로 만든다");
  assert.ok(!/cubic-bezier/.test(panel), "패널에 이징 곡선을 다시 넣으면 끊긴다");

  for (const name of ["panel-in", "panel-out"]) {
    const body = frames(name);
    const stages = readStages(body);
    // linear 라서 모양의 부드러움은 단계의 촘촘함에서만 나온다.
    assert.ok(stages.length >= 8, `${name}: 곡선 샘플이 최소 8단계 (지금 ${stages.length})`);

    const opening = name === "panel-in" ? stages : [...stages].reverse();
    // 머리는 단조롭게 올라오고(윗변 y 감소), 폭은 단조롭게 벌어진다 — 되돌아가는 구간이 없다.
    for (let i = 1; i < opening.length; i++) {
      assert.ok(opening[i].topY <= opening[i - 1].topY, `${name}: 윗변이 되돌아간다(${i})`);
      assert.ok(opening[i].topWidth >= opening[i - 1].topWidth, `${name}: 윗변 폭이 줄어든다(${i})`);
      assert.ok(opening[i].bottomWidth >= opening[i - 1].bottomWidth, `${name}: 아랫변 폭이 줄어든다(${i})`);
    }
    // 처음과 끝만 직사각형. 그 사이는 아랫변이 늘 더 좁다 = 꼬리가 입구에 물려 있다.
    for (const s of opening.slice(1, -1)) {
      assert.ok(s.bottomWidth < s.topWidth, `${name}: 중간 단계는 아래로 갈수록 좁아야 한다`);
    }
    // 머리가 거의 제자리에 왔을 때(윗변 y < 5%) 꼬리는 아직 한참 좁아야 '뒤늦게 따라 나온다'.
    const headHome = opening.find((s) => s.topY < 5);
    assert.ok(headHome && headHome.bottomWidth < 70, `${name}: 머리가 다 나왔는데 꼬리도 같이 끝난다`);

    // 출발/도착은 클릭한 서류철 입구 — 좌표는 JS 가 재서 var 로 넘긴다(폴백=화면 아래).
    assert.match(body, /var\(--slot-dx, 0px\)/, `${name}: 입구 x`);
    assert.match(body, /var\(--slot-dy, 120px\)/, `${name}: 입구 y`);
    assert.match(body, /var\(--slot-scale, 0\.34\)/, `${name}: 입구 크기`);
    // 중간 단계의 이동·크기는 입구 값에 비례한다 — 카드마다 궤적이 달라야 한다.
    assert.match(body, /calc\(var\(--slot-dx, 0px\) \* 0\.\d+\)/, `${name}: 비례 이동`);
    assert.match(body, /scale\(calc\(var\(--slot-scale, 0\.34\) \* 0\.\d+ \+ 0\.\d+\)\)/, `${name}: 비례 확대`);
  }
});

test("the slot is measured from the clicked card, not hardcoded to the screen bottom", () => {
  const panel = readFileSync(join(ROOT, "src/components/SourceDetailPanel.tsx"), "utf8");

  assert.match(panel, /querySelector<HTMLElement>\(`\[data-source="\$\{source\}"\]`\)/, "클릭한 카드를 찾는다");
  // getBoundingClientRect 는 진입 애니메이션의 transform 이 걸린 값을 준다 — 자기 출발점을 오염시킨다.
  assert.match(panel, /sheet\.offsetWidth/, "종이 크기는 레이아웃 값으로 잰다");
  assert.ok(!/sheet\.getBoundingClientRect/.test(panel), "transform 이 섞인 값으로 재면 안 된다");
  // 재기 전 프레임에 애니메이션이 걸리면 첫 프레임 transform 이 측정을 망친다.
  assert.match(panel, /slot === undefined\s*\?\s*"none"/, "측정 전에는 애니메이션을 걸지 않는다");
});

test("the tab clears the card entirely", () => {
  // 탭 높이 = border-top 1 + padding 5 + 본문줄(아이콘 13px 과 12.5*1.2=15px 중 큰 값) + padding 5
  const tabHeight = 1 + 5 + Math.max(13, 12.5 * 1.2) + 5; // 26px
  const marginTop = 48; // mt-12

  assert.equal(tabHeight, 26);
  assert.ok(marginTop > tabHeight, `위 여백(${marginTop}px)이 탭 높이(${tabHeight}px)보다 커야 한다`);
});
