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
