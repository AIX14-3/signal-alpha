"use client";

import { create } from "zustand";
import { getAnalysisStatus, type AnalysisStatus } from "@/lib/apiClient";

type AnalysisState = {
  ticker: string | null;
  status: AnalysisStatus | null;
  polling: boolean;
  start: (ticker: string) => void;
  poll: () => Promise<void>;
  reset: () => void;
};

export const useAnalysisStore = create<AnalysisState>((set, get) => ({
  ticker: null,
  status: null,
  polling: false,

  start(ticker) {
    set({ ticker, status: null, polling: true });
  },

  async poll() {
    const ticker = get().ticker;
    if (!ticker) return;
    try {
      const status = await getAnalysisStatus(ticker);
      const done = status.overall === "success" || status.overall === "failed";
      set({ status, polling: !done });
    } catch {
      set({ polling: false });
    }
  },

  reset() {
    set({ ticker: null, status: null, polling: false });
  },
}));
