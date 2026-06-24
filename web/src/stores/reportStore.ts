"use client";

import { create } from "zustand";
import { getQuota, getReport, issueReport, type Quota, type Report } from "@/lib/apiClient";

type ReportState = {
  report: Report | null;
  quota: Quota | null;
  loading: boolean;
  issuing: boolean;
  error: string | null;
  load: (stockCode: string) => Promise<void>;
  issue: (stockCode: string) => Promise<Report>;
  loadQuota: () => Promise<void>;
};

export const useReportStore = create<ReportState>((set) => ({
  report: null,
  quota: null,
  loading: false,
  issuing: false,
  error: null,

  async load(stockCode) {
    set({ loading: true, error: null, report: null });
    try {
      set({ report: await getReport(stockCode), loading: false });
    } catch (error) {
      set({ loading: false, error: (error as Error).message });
    }
  },

  async issue(stockCode) {
    set({ issuing: true, error: null });
    try {
      const report = await issueReport(stockCode);
      set({ report, issuing: false });
      return report;
    } catch (error) {
      set({ issuing: false });
      throw error;
    }
  },

  async loadQuota() {
    try {
      set({ quota: await getQuota() });
    } catch {
      set({ quota: null });
    }
  },
}));
