"use client";

import { create } from "zustand";
import {
  getNewsSummary,
  getStockPrices,
  listRecentNews,
  listStockNews,
  listStocks,
  type NewsSummary,
  type RecentNewsItem,
  type Stock,
  type StockNewsDigest,
  type StockNewsItem,
  type StockPriceSeries,
} from "@/lib/apiClient";

// 홈 v2 상태. 좌 pane = 전역 뉴스 피드(#798), 우 pane = 3섹션(관심/실시간 분석/커뮤니티 인기).
// "실시간 분석 종목" 아코디언이 selectedCode 로 열리며, 뉴스+가격은 여기서, 리포트는 reportStore 가 담당.
type HomeState = {
  summary: NewsSummary | null;
  feed: RecentNewsItem[];
  chips: Stock[];
  loading: boolean;
  error: string | null;
  selectedCode: string | null;
  stockNews: { count: number; items: StockNewsItem[]; digest: StockNewsDigest | null } | null;
  stockNewsLoading: boolean;
  stockNewsError: string | null;
  stockPrices: StockPriceSeries | null;
  stockPricesLoading: boolean;
  init: () => Promise<void>;
  select: (code: string) => Promise<void>;
  toggle: (code: string) => Promise<void>;
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
  stockPrices: null,
  stockPricesLoading: false,

  async init() {
    // 이미 로드됐으면(피드 존재) 재진입 시 재요청하지 않는다.
    if (get().feed.length > 0 || get().loading) return;
    set({ loading: true, error: null });
    try {
      // 헤더 배너 + 좌 피드 + 실시간 분석 종목 리스트를 병렬 로드(NFR-3).
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
      // 좌측 첫 항목의 종목을 자동 선택(아코디언 자동 열기).
      const first = recent.items.find((n) => n.stock_code)?.stock_code;
      if (first) void get().select(first);
    } catch (error) {
      set({ loading: false, error: (error as Error).message });
    }
  },

  async select(code) {
    const token = ++selectToken;
    set({
      selectedCode: code,
      stockNewsLoading: true,
      stockNewsError: null,
      stockPricesLoading: true,
    });
    // 뉴스 + 가격 병렬 페치(NFR-3). 한쪽 실패는 상대에 영향 없음.
    void listStockNews(code, 20)
      .then((data) => {
        if (token !== selectToken) return; // 더 최신 선택이 있으면 폐기
        set({ stockNews: data, stockNewsLoading: false });
      })
      .catch((error) => {
        if (token !== selectToken) return;
        // 이전 종목 뉴스가 남아 오해를 주지 않도록 비운다(가격 catch 와 대칭).
        set({ stockNews: null, stockNewsLoading: false, stockNewsError: (error as Error).message });
      });
    void getStockPrices(code)
      .then((data) => {
        if (token !== selectToken) return;
        set({ stockPrices: data, stockPricesLoading: false });
      })
      .catch(() => {
        if (token !== selectToken) return;
        // 가격 미동기화/실패는 빈 상태로 안전 처리("차트 준비 중").
        set({ stockPrices: null, stockPricesLoading: false });
      });
  },

  async toggle(code) {
    // 아코디언: 열린 종목 재클릭이면 접고, 아니면 그 종목을 선택해 연다.
    if (get().selectedCode === code) {
      set({ selectedCode: null });
      return;
    }
    await get().select(code);
  },
}));
