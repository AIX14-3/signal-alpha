"use client";

import { create } from "zustand";
import { getReport, type Report } from "@/lib/apiClient";

type ReportState = {
  report: Report | null;
  loading: boolean;
  error: string | null;
  load: (stockCode: string) => Promise<void>;
};

export const useReportStore = create<ReportState>((set) => ({
  report: null,
  loading: false,
  error: null,

  async load(stockCode) {
    set({ loading: true, error: null, report: null });
    try {
      set({ report: await getReport(stockCode), loading: false });
    } catch (error) {
      set({ loading: false, error: (error as Error).message });
    }
  },
}));
