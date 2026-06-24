"use client";

import { create } from "zustand";
import { addWatchlist, listWatchlists, removeWatchlist, type WatchlistItem } from "@/lib/apiClient";

type WatchlistState = {
  items: WatchlistItem[];
  count: number;
  loading: boolean;
  error: string | null;
  load: () => Promise<void>;
  add: (stockCode: string) => Promise<void>;
  remove: (stockCode: string) => Promise<void>;
};

// 신규 기획: 관심종목 무제한(한도 없음).
export const useWatchlistStore = create<WatchlistState>((set, get) => ({
  items: [],
  count: 0,
  loading: false,
  error: null,

  async load() {
    set({ loading: true, error: null });
    try {
      const data = await listWatchlists();
      set({ items: data.items, count: data.count, loading: false });
    } catch (error) {
      set({ loading: false, error: (error as Error).message });
    }
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
