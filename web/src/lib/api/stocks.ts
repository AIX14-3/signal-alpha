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

// 종목 뉴스 다이제스트(LLM 종합 한 줄). 워커가 요약하며, 미생성 종목은 null.
export type StockNewsDigest = {
  text: string | null;
  model: string | null;
  article_count: number;
  generated_at: string | null;
};

// 종목별 최신 뉴스 목록 + 건수 + 다이제스트. 미수집 종목은 count=0, digest=null.
export async function listStockNews(
  stockCode: string,
  limit = 20,
): Promise<{ count: number; items: StockNewsItem[]; digest: StockNewsDigest | null }> {
  return apiFetch(`/api/stocks/${encodeURIComponent(stockCode)}/news?limit=${limit}`, {
    auth: "none",
  });
}

// 전역 뉴스 집계(공개) — 홈 "뉴스 N건을 분석한 시그널" 헤더용. total_articles=총 분석 기사수.
export type NewsSummary = {
  total_articles: number;
  stock_count: number;
  recent_articles: number;
  latest_collected_at: string | null;
  recent_hours: number;
  notice: string;
};

// recentHours 로 recent_articles 집계 창 조절(기본 24h, 서버가 1h~30d 로 클램프).
export async function getNewsSummary(recentHours = 24): Promise<NewsSummary> {
  return apiFetch(`/api/news/summary?recent_hours=${recentHours}`, { auth: "none" });
}

// 전역 최신 뉴스 피드(공개) — 홈 2-pane 좌측 목록용. 종목명 포함(미매핑은 null).
export type RecentNewsItem = StockNewsItem & {
  stock_code: string;
  stock_name: string | null;
};

export async function listRecentNews(limit = 30): Promise<{ items: RecentNewsItem[] }> {
  return apiFetch(`/api/news/recent?limit=${limit}`, { auth: "none" });
}
