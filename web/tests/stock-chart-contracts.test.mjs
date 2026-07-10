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
