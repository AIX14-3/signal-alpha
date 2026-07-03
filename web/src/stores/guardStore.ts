"use client";

import { create } from "zustand";
import { getGuardStatus, type GuardScope } from "@/lib/apiClient";

// 지정학 리스크 Kill-Switch 상태 폴링. 상태 API 장애 시엔 차단하지 않는다(fail-open) —
// 차단 인프라 장애가 서비스 전체를 막는 더 큰 장애로 번지지 않게 하기 위함.
// 단, 단발성 실패(배포 롤오버·일시적 네트워크 blip)로 활성 차단이 순간 풀리지 않도록,
// 마지막 성공 상태를 유지하고 연속 실패가 임계치를 넘을 때만 fail-open 한다.
const POLL_INTERVAL_MS = 45_000;
const FAIL_OPEN_AFTER = 2;

type GuardState = {
  status: "ok" | "blocked";
  scope: GuardScope;
  reason: string | null;
  resumeAt: string | null;
  startPolling: () => void;
  stopPolling: () => void;
};

let pollTimer: ReturnType<typeof setInterval> | null = null;
let consecutiveErrors = 0;

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
        consecutiveErrors = 0;
        set({
          status: data.status,
          scope: data.scope,
          reason: data.reason,
          resumeAt: data.resume_at,
        });
      } catch {
        // 연속 실패가 임계치를 넘기 전까지는 마지막으로 성공한 상태를 유지한다.
        consecutiveErrors += 1;
        if (consecutiveErrors >= FAIL_OPEN_AFTER) {
          set({ status: "ok", scope: "report_generation", reason: null, resumeAt: null });
        }
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
    consecutiveErrors = 0;
  },
}));
