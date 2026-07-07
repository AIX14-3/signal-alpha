// 종목/관심종목(무제한).

import { apiFetch } from "./core";

export type Stock = {
  id: number;
  stock_code: string;
  stock_name: string;
  market: string | null;
  sector: string | null;
};

export type WatchlistItem = { stock: Stock; created_at: string | null };

export async function searchStocks(query: string): Promise<{ items: Stock[] }> {
  return apiFetch(`/api/stocks/search?query=${encodeURIComponent(query)}`, { auth: "none" });
}

export async function listStocks(limit = 100): Promise<{ items: Stock[] }> {
  return apiFetch(`/api/stocks?limit=${limit}`, { auth: "none" });
}

export async function listWatchlists(): Promise<{ count: number; items: WatchlistItem[] }> {
  return apiFetch("/api/watchlists");
}

export async function addWatchlist(stockCode: string): Promise<WatchlistItem> {
  return apiFetch("/api/watchlists", { method: "POST", body: JSON.stringify({ stock_code: stockCode }) });
}

export async function removeWatchlist(stockCode: string): Promise<{ status: string }> {
  return apiFetch(`/api/watchlists/${encodeURIComponent(stockCode)}`, { method: "DELETE" });
}

/* ===== 종목 뉴스(공개) ===== */
export type StockNewsItem = {
  title: string | null;
  summary: string | null;
  url: string | null;
  press: string | null;
  source: string | null;
  published_at: string | null;
};

// 종목별 최신 뉴스 목록 + 건수. 워커 뉴스 데몬이 적재하며, 미수집 종목은 count=0.
export async function listStockNews(
  stockCode: string,
  limit = 20,
): Promise<{ count: number; items: StockNewsItem[] }> {
  return apiFetch(`/api/stocks/${encodeURIComponent(stockCode)}/news?limit=${limit}`, {
    auth: "none",
  });
}

// 전역 뉴스 집계. 토스식 "뉴스 N건을 분석한 시그널" 헤더 소스. recent=창 내 건수(헤드라인).
export type NewsSummary = {
  total: number;
  recent: number;
  recent_stock_count: number;
  window_hours: number;
};

export async function getNewsSummary(windowHours = 24): Promise<NewsSummary> {
  return apiFetch(`/api/news/summary?window_hours=${windowHours}`, { auth: "none" });
}
