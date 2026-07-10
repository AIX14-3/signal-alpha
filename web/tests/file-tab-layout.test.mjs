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

// x 좌표는 `12.3%` 또는 `calc(12.3% + 0.64 * var(--tail-lean, 0%))` 이다. 기준 좌표와 기울기
// 가중치를 함께 뽑아낸다. var() 안의 쉼표 때문에 단순 split(",") 은 못 쓴다.
const COORD = /(?:calc\(([\d.-]+)% \+ ([\d.]+) \* var\(--tail-lean, 0%\)\)|([\d.-]+)%) ([\d.-]+)%/g;

// 한 키프레임의 clip-path polygon 을 읽는다. 점 순서 = 오른쪽 위→아래, 왼쪽 아래→위(시계방향).
function readStages(body) {
  return [...body.matchAll(/clip-path: (polygon\(.+?\));/g)].map(([, raw]) => {
    const pts = [...raw.matchAll(COORD)].map(([, leanBase, weight, plain, y]) => ({
      x: Number.parseFloat(leanBase ?? plain),
      y: Number.parseFloat(y),
      lean: weight ? Number.parseFloat(weight) : 0,
    }));
    const half = pts.length / 2;
    const side = pts.slice(0, half); // 오른쪽 옆선, 위 → 아래
    const [topRight, topLeft] = [pts[0], pts.at(-1)];
    const [bottomRight, bottomLeft] = [pts[half - 1], pts[half]];
    const span = bottomRight.y - topRight.y;

    // '좁아지는 구간' = 폭이 아직 만폭인 마지막 지점부터 뾰족한 끝까지, 높이 대비 비율.
    // (90% 같은 헐거운 문턱으로 재면 곡선의 완만한 초입을 놓쳐 구간이 실제보다 짧게 잡힌다.)
    const widest = Math.max(topRight.x, bottomRight.x) - 50;
    const full = side.filter((pt) => pt.x - 50 >= widest * 0.98);
    const pointyAtTop = topRight.x < bottomRight.x;
    const edge = pointyAtTop
      ? Math.min(...full.map((pt) => pt.y))
      : Math.max(...full.map((pt) => pt.y));
    const narrowZone =
      span > 0 && full.length && full.length < half
        ? (pointyAtTop ? edge - topRight.y : bottomRight.y - edge) / span
        : 0;

    return {
      points: pts.length,
      topY: topRight.y,
      topWidth: topRight.x - topLeft.x,
      bottomWidth: bottomRight.x - bottomLeft.x,
      narrowZone,
      // 옆선이 곧으면 중간 점이 위·아래 폭의 선형 보간과 일치한다. 휘어야 각이 안 진다.
      sideBow: Math.max(
        ...side.map((pt, i) => Math.abs(pt.x - (topRight.x + (bottomRight.x - topRight.x) * (i / (half - 1))))),
      ),
      // 기울기 가중치: 뾰족한 끝에서 1, 반대편에서 0 이어야 그 끝이 슬롯으로 끌려간다.
      leanAtTop: topRight.lean,
      leanAtBottom: bottomRight.lean,
    };
  });
}

function frames(css, name) {
  // 블록 끝 = 들여쓰기 없는 `}`. 줄바꿈 문자로 자르면 CRLF 파일에서 두 키프레임을 통째로 읽는다.
  const block = new RegExp(`@keyframes ${name} \\{[\\s\\S]*?\\r?\\n\\}`).exec(css);
  assert.ok(block, `@keyframes ${name} 를 찾지 못했다`);
  return block[0];
}

test("the paper motion is one linear curve baked into keyframe values", () => {
  const panel = readFileSync(join(ROOT, "src/components/SourceDetailPanel.tsx"), "utf8");
  // 이징 곡선을 주면 구간마다 재적용돼 감속→가속이 되풀이된다(끊김).
  assert.match(panel, /const PAPER_MOTION = "linear"/, "궤적은 linear + 키프레임 값으로 만든다");
  assert.ok(!/cubic-bezier/.test(panel), "패널에 이징 곡선을 다시 넣으면 끊긴다");
});

test("opening: head first, thin tail last, tail leans toward the slot", () => {
  const css = readFileSync(join(ROOT, "src/app/globals.css"), "utf8");
  const body = frames(css, "panel-in");
  const stages = readStages(body);

  assert.ok(stages.length >= 8, `곡선 샘플이 최소 8단계 (지금 ${stages.length})`);
  for (const s of stages) assert.ok(s.points >= 16, `옆선 샘플 부족(점 ${s.points}) — 각져 보인다`);

  // 되돌아가는 구간 없이 단조롭게 펴진다.
  for (let i = 1; i < stages.length; i++) {
    assert.ok(stages[i].topY <= stages[i - 1].topY, `윗변이 되돌아간다(${i})`);
    assert.ok(stages[i].topWidth >= stages[i - 1].topWidth, `윗변 폭이 줄어든다(${i})`);
    assert.ok(stages[i].bottomWidth >= stages[i - 1].bottomWidth, `아랫변 폭이 줄어든다(${i})`);
  }
  // 중간 단계는 늘 아래가 더 좁다 = 꼬리가 입구에 물려 있다.
  for (const s of stages.slice(1, -1)) {
    assert.ok(s.bottomWidth < s.topWidth, "나올 땐 아래로 갈수록 좁아야 한다");
  }
  // 머리가 제자리에 와도(윗변 y < 5%) 꼬리는 아직 좁아야 '뒤늦게 따라 나온다'.
  const headHome = stages.find((s) => s.topY < 5);
  assert.ok(headHome && headHome.bottomWidth < 60, "머리가 다 나왔는데 꼬리도 같이 끝난다");

  // 삼각형 → 사다리꼴 → 직사각형.
  const pinch = stages.map((s) => s.bottomWidth / s.topWidth);
  assert.ok(pinch[0] < 0.15, `처음엔 아랫변이 거의 한 점(삼각형)이어야 한다 — ${pinch[0].toFixed(2)}`);
  assert.ok(pinch.some((r) => r > 0.2 && r < 0.9), "삼각형과 직사각형 사이에 사다리꼴 구간이 없다");
  assert.ok(pinch.at(-1) > 0.99, "끝은 반듯한 직사각형이어야 한다");

  // 옆선이 휘어야 각이 안 진다(직선이면 sideBow = 0).
  const bow = Math.max(...stages.slice(1, -1).map((s) => s.sideBow));
  assert.ok(bow > 2, `옆선이 거의 직선이다(최대 휨 ${bow.toFixed(1)}%)`);

  // 꼬리는 종이의 절반쯤이어야 한다 — 너무 짧으면 뭉툭, 너무 길면 목이 늘어진다.
  const settled = stages.filter((s) => s.topY < 10 && s.bottomWidth < s.topWidth * 0.99);
  assert.ok(settled.length > 0, "자리를 잡아가는 단계가 있어야 한다");
  // 옆선 샘플이 성글어 실제 c(≈0.5~0.6)보다 크게 잡힌다 — 그 오차를 감안한 창.
  for (const s of settled) {
    assert.ok(s.narrowZone > 0.35 && s.narrowZone < 0.75, `꼬리 길이가 절반 근처가 아니다(${s.narrowZone.toFixed(2)})`);
  }

  // 슬롯으로 끌려가는 쪽은 뾰족한 끝(아래)이다. 위는 안 끌린다.
  for (const s of stages.slice(0, -1)) {
    assert.ok(s.leanAtBottom > s.leanAtTop, "나올 땐 꼬리(아래)가 슬롯 쪽으로 쏠려야 한다");
  }
  assert.equal(stages[0].leanAtTop, 0, "머리는 슬롯 쪽으로 끌리지 않는다");
  // 다 펴진 종이는 기울지 않는다 — 안 그러면 직사각형이 평행사변형으로 잘린다.
  assert.equal(stages.at(-1).leanAtBottom, 0, "제자리에 앉은 종이의 아랫변이 슬롯 쪽으로 밀린다");
  assert.equal(stages.at(-1).leanAtTop, 0, "제자리에 앉은 종이의 윗변이 슬롯 쪽으로 밀린다");
});

test("closing is not the reverse: the head narrows as it is sucked back in", () => {
  const css = readFileSync(join(ROOT, "src/app/globals.css"), "utf8");
  const stages = readStages(frames(css, "panel-out"));

  assert.ok(stages.length >= 8, `곡선 샘플이 최소 8단계 (지금 ${stages.length})`);
  // 나올 땐 아래가, 들어갈 땐 위가 좁다. 역재생이면 이 단언이 뒤집힌다.
  for (const s of stages.slice(1, -1)) {
    assert.ok(s.topWidth < s.bottomWidth, "들어갈 땐 윗부분이 좁아져야 한다");
  }
  // 마지막엔 머리가 거의 한 점.
  assert.ok(stages.at(-1).topWidth / stages.at(-1).bottomWidth < 0.15, "끝에서 머리가 한 점으로 모이지 않는다");
  // 이번엔 머리(위)가 슬롯으로 끌려간다. 단 첫 프레임(반듯한 종이)은 기울지 않는다.
  assert.equal(stages[0].leanAtTop, 0, "닫히기 직전의 종이가 이미 기울어 있다");
  for (const s of stages.slice(1)) {
    assert.ok(s.leanAtTop > s.leanAtBottom, "들어갈 땐 머리(위)가 슬롯 쪽으로 쏠려야 한다");
  }
});

test("the lean comes from the clicked card's horizontal position", () => {
  const panel = readFileSync(join(ROOT, "src/components/SourceDetailPanel.tsx"), "utf8");
  assert.match(panel, /"--tail-lean": `\$\{slot\.lean\.toFixed\(1\)\}%`/, "기울기를 CSS 로 넘긴다");
  assert.match(panel, /dx \/ \(window\.innerWidth \/ 2\)/, "화면 중앙 대비 좌우 위치로 계산한다");
  assert.match(panel, /Math\.max\(-1, Math\.min\(1,/, "-1~1 로 묶는다");
});
