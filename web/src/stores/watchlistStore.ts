"use client";

import { create } from "zustand";
import { addWatchlist, listWatchlists, removeWatchlist, type WatchlistItem } from "@/lib/apiClient";

type WatchlistState = {
  items: WatchlistItem[];
  count: number;
  loading: boolean;
  loaded: boolean;
  error: string | null;
  load: () => Promise<void>;
  ensureLoaded: () => Promise<void>;
  reset: () => void;
  add: (stockCode: string) => Promise<void>;
  remove: (stockCode: string) => Promise<void>;
};

// 신규 기획: 관심종목 무제한(한도 없음).
export const useWatchlistStore = create<WatchlistState>((set, get) => ({
  items: [],
  count: 0,
  loading: false,
  loaded: false,
  error: null,

  async load() {
    set({ loading: true, error: null });
    try {
      const data = await listWatchlists();
      set({ items: data.items, count: data.count, loading: false, loaded: true });
    } catch (error) {
      set({ loading: false, error: (error as Error).message });
    }
  },

  // 여러 화면(리포트 버튼·스트립·관심종목 페이지)이 동시에 마운트돼도 목록을 한 번만 받아온다.
  async ensureLoaded() {
    const { loaded, loading } = get();
    if (loaded || loading) return;
    await get().load();
  },

  // 로그아웃 시 이전 계정의 관심종목이 남아 다음 사용자에게 보이지 않도록 비운다.
  reset() {
    set({ items: [], count: 0, loading: false, loaded: false, error: null });
  },

  async add(stockCode) {
    await addWatchlist(stockCode);
    await get().load();
  },

  async remove(stockCode) {
    await removeWatchlist(stockCode);
    await get().load();
  },
}));
