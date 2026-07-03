"use client";

import { create } from "zustand";
import { getGuardStatus, type GuardScope } from "@/lib/apiClient";

// 지정학 리스크 Kill-Switch 상태 폴링. 상태 API 장애 시엔 차단하지 않는다(fail-open) —
// 차단 인프라 장애가 서비스 전체를 막는 더 큰 장애로 번지지 않게 하기 위함.
const POLL_INTERVAL_MS = 45_000;

type GuardState = {
  status: "ok" | "blocked";
  scope: GuardScope;
  reason: string | null;
  resumeAt: string | null;
  startPolling: () => void;
  stopPolling: () => void;
};

let pollTimer: ReturnType<typeof setInterval> | null = null;

export const useGuardStore = create<GuardState>((set) => ({
  status: "ok",
  scope: "report_generation",
  reason: null,
  resumeAt: null,

  startPolling() {
    if (pollTimer) return;
    const poll = async () => {
      try {
        const data = await getGuardStatus();
        set({
          status: data.status,
          scope: data.scope,
          reason: data.reason,
          resumeAt: data.resume_at,
        });
      } catch {
        set({ status: "ok" });
      }
    };
    void poll();
    pollTimer = setInterval(() => void poll(), POLL_INTERVAL_MS);
  },

  stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  },
}));
