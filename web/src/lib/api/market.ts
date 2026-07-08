// 시장 지수(코스피·코스닥·원/달러·VIX) — 리포트 상단 미니차트용(item 7).

import { apiFetch } from "./core";

export type IndexBar = { time: number; close: number };
export type MarketIndex = {
  key: string;
  name: string;
  last: number;
  change: number;
  change_pct: number;
  bars: IndexBar[];
  is_demo: boolean;
};

export async function getMarketIndices(): Promise<{ indices: MarketIndex[] }> {
  return apiFetch(`/api/market/indices`, { auth: "none" });
}
