import { readFileSync } from "node:fs";
import { join } from "node:path";
import test from "node:test";
import assert from "node:assert/strict";

const ROOT = process.cwd();
const SOURCE = readFileSync(join(ROOT, "src/components/StockChart.tsx"), "utf8");

function assertIncludes(expected, context) {
  assert.ok(SOURCE.includes(expected), `${context} should include ${JSON.stringify(expected)}`);
}

test("intraday bars are shifted to KST before lightweight-charts renders them", () => {
  // lightweight-charts 는 숫자 time 을 UTCTimestamp 로 보고 UTC 로 눈금을 찍는다. 분봉에
  // 유닉스 초를 그대로 넘기면 장중 10:50 이 01:50 로 보인다.
  assertIncludes("const KST_OFFSET_SEC = 9 * 60 * 60", "KST 고정 오프셋");
  assertIncludes("bar.time + KST_OFFSET_SEC", "분봉 타임스탬프 보정");
  assertIncludes('toSeriesData(data.bars, chartType, tf === "min")', "보정은 분봉에만");
  // 일/월/년 봉은 서버가 'YYYY-MM-DD' 문자열을 주므로 보정 대상이 아니다.
  assertIncludes('typeof bar.time === "number"', "문자열 날짜는 건드리지 않는다");
});

test("chart shows that it is live: intraday default in market hours + current-price line", () => {
  assertIncludes('useState(() => (isMarketOpen() ? "min" : "day"))', "장중 기본 탭 = 분봉");
  assertIncludes("priceLineVisible: true", "현재가 수평선");
  assertIncludes("lastValueVisible: true", "우측 축 현재가 라벨");
  assert.ok(
    !SOURCE.includes("priceLineVisible: false"),
    "영역/라인에서 현재가 수평선을 끄면 갱신이 눈에 보이지 않는다",
  );
});

test("KST offset math lands on the Korean trading session", () => {
  // 서버가 실제로 준 마지막 분봉(2026-07-10 장중)의 유닉스 초.
  const bar = 1_783_646_387;
  const utcHour = new Date(bar * 1000).getUTCHours();
  const shifted = new Date((bar + 9 * 60 * 60) * 1000).getUTCHours();

  assert.equal(utcHour, 1, "보정 전에는 새벽 1시대로 찍힌다");
  assert.equal(shifted, 10, "보정 후에는 장중 10시대");
});

test("market indices follow the Korean quote convention: up red, down blue", () => {
  const source = readFileSync(join(ROOT, "src/components/MarketIndices.tsx"), "utf8");

  // 지수도 '시세'다 — StockChart 와 같은 색을 쓴다. 제품의 방향 의미색(up=초록)과 섞으면
  // 같은 화면에서 빨강이 상승도 되고 하락도 된다.
  assert.match(source, /const KR_UP = "#ef4444"/, "상승 = 빨강");
  assert.match(source, /const KR_DOWN = "#3b82f6"/, "하락 = 파랑");
  assert.ok(!source.includes("#16a34a"), "상승 초록(의미색)을 시세에 쓰지 않는다");
  assert.ok(!source.includes("#dc2626"), "하락 빨강(의미색)을 시세에 쓰지 않는다");

  const chart = readFileSync(join(ROOT, "src/components/StockChart.tsx"), "utf8");
  for (const color of ['const KR_UP = "#ef4444"', 'const KR_DOWN = "#3b82f6"']) {
    assert.ok(chart.includes(color), `StockChart 와 색이 갈라지면 안 된다: ${color}`);
  }
});
