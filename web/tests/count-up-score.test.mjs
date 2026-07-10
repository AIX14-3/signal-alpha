import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

const ROOT = process.cwd();
const SOURCE = readFileSync(join(ROOT, "src/components/CountUpScore.tsx"), "utf8");

test("the score rolls up like a gauge, then settles on the real value", () => {
  assert.match(SOURCE, /requestAnimationFrame/, "프레임마다 갱신해야 굴러가는 것처럼 보인다");
  assert.match(SOURCE, /cancelAnimationFrame/, "언마운트 시 프레임을 정리한다");
  assert.match(SOURCE, /1 - \(1 - t\) \*\* 3/, "끝에서 감속(계기판 바늘이 목표에 붙는 느낌)");
  // 표시 형식은 한 곳(scoreText)만 쓴다 — 애니메이션 중에도 최종값과 같은 규칙이어야 한다.
  assert.match(SOURCE, /import \{ scoreText \} from "@\/lib\/format"/, "표시 형식 단일 출처");
  assert.ok(!/toFixed\(/.test(SOURCE), "여기서 직접 반올림하면 최종값과 규칙이 갈라진다");
});

test("the gauge does not jitter and honours reduced motion", () => {
  // 자릿수마다 글자 폭이 다르면 숫자가 좌우로 흔들려 계기판이 아니라 덜컹인다.
  assert.match(SOURCE, /fontVariantNumeric: "tabular-nums"/, "고정폭 숫자");
  assert.match(SOURCE, /prefers-reduced-motion: reduce/, "모션 최소화 설정을 존중한다");
  assert.match(SOURCE, /if \(settled == null \|\| prefersReducedMotion\(\)\)/, "그 경우 즉시 최종값");
});

test("the report page shows the headline score through the gauge", () => {
  const page = readFileSync(join(ROOT, "src/app/report/[ticker]/page.tsx"), "utf8");
  assert.match(page, /<CountUpScore value=\{report\.score\}/, "종합 점수는 계기판으로 표시");
  // 소스별 카드 점수는 그대로 정적 표시(여섯 개가 한꺼번에 굴러가면 산만하다).
  assert.match(page, /\{scoreText\(src\.score\)\}/, "소스 카드 점수는 정적");
});
