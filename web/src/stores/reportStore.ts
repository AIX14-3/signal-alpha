"use client";

import { create } from "zustand";
import { getReport, type Report } from "@/lib/apiClient";

type ReportState = {
  report: Report | null;
  loading: boolean;
  error: string | null;
  load: (stockCode: string) => Promise<void>;
};

// stale 가드: 빠른 연속 load(홈 아코디언 전환 등) 시 마지막 요청만 반영. React 상태 밖 모듈 카운터.
let loadToken = 0;

export const useReportStore = create<ReportState>((set) => ({
  report: null,
  loading: false,
  error: null,

  async load(stockCode) {
    const token = ++loadToken;
    set({ loading: true, error: null, report: null });
    try {
      const report = await getReport(stockCode);
      if (token !== loadToken) return; // 더 최신 load 가 있으면 폐기(뒤늦은 이전 종목 리포트가 덮는 것 방지)
      set({ report, loading: false });
    } catch (error) {
      if (token !== loadToken) return;
      set({ loading: false, error: (error as Error).message });
    }
  },
}));
