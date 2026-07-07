"use client";

import { create } from "zustand";
import {
  getNewsSummary,
  listRecentNews,
  listStockNews,
  listStocks,
  type NewsSummary,
  type RecentNewsItem,
  type Stock,
  type StockNewsItem,
} from "@/lib/apiClient";

// 홈 2-pane 상태. 좌 pane = 전역 뉴스 피드(#798) + 종목 칩(검색), 우 pane = 선택 종목 뉴스.
// "왜 이 신호" 리포트는 reportStore(재사용)가 담당 — 여기서는 selectedCode 만 노출한다.
type HomeState = {
  summary: NewsSummary | null;
  feed: RecentNewsItem[];
  chips: Stock[];
  loading: boolean;
  error: string | null;
  selectedCode: string | null;
  stockNews: { count: number; items: StockNewsItem[] } | null;
  stockNewsLoading: boolean;
  stockNewsError: string | null;
  init: () => Promise<void>;
  select: (code: string) => Promise<void>;
};

// FR-4 stale 가드: 빠른 연속 선택 시 마지막 요청만 반영. React 상태 밖 모듈 카운터.
let selectToken = 0;

export const useHomeStore = create<HomeState>((set, get) => ({
  summary: null,
  feed: [],
  chips: [],
  loading: false,
  error: null,
  selectedCode: null,
  stockNews: null,
  stockNewsLoading: false,
  stockNewsError: null,

  async init() {
    // 이미 로드됐으면(피드 존재) 재진입 시 재요청하지 않는다.
    if (get().feed.length > 0 || get().loading) return;
    set({ loading: true, error: null });
    try {
      // FR-5 헤더 배너 + 좌 피드 + 종목 칩을 병렬 로드(NFR-3).
      const [summary, recent, stocks] = await Promise.all([
        getNewsSummary().catch(() => null),
        listRecentNews(30),
        listStocks(30).catch(() => ({ items: [] as Stock[] })),
      ]);
      set({
        summary,
        feed: recent.items,
        chips: stocks.items,
        loading: false,
      });
      // Q1: 좌측 첫 항목의 종목을 자동 선택.
      const first = recent.items.find((n) => n.stock_code)?.stock_code;
      if (first) void get().select(first);
    } catch (error) {
      set({ loading: false, error: (error as Error).message });
    }
  },

  async select(code) {
    const token = ++selectToken;
    set({ selectedCode: code, stockNewsLoading: true, stockNewsError: null });
    try {
      const data = await listStockNews(code, 20);
      if (token !== selectToken) return; // 더 최신 선택이 있으면 폐기
      set({ stockNews: data, stockNewsLoading: false });
    } catch (error) {
      if (token !== selectToken) return;
      set({ stockNewsLoading: false, stockNewsError: (error as Error).message });
    }
  },
}));
