"use client";

// 매매 부검 스토어 — 수기 체결 입력·계획·단건/패턴 부검. 로그인 회원 누구나 이용(구독 게이트 없음).
// 소비 컴포넌트는 user 로만 분기한다(비로그인만 401).

import { create } from "zustand";
import {
  type FillCreateBody,
  type PatternSummary,
  type TradeFill,
  type TradePlan,
  type TradePlanBody,
  type TradePostmortem,
  createFill,
  deleteFill,
  deleteTradePlan,
  getPatterns,
  getTradePostmortem,
  listFills,
  listTradePlans,
  upsertTradePlan,
} from "@/lib/apiClient";

type PostmortemState = {
  fills: TradeFill[];
  plans: TradePlan[];
  patterns: PatternSummary | null;
  trade: TradePostmortem | null;
  loading: boolean;
  error: string | null;

  loadOverview: () => Promise<void>;
  addFill: (body: FillCreateBody) => Promise<void>;
  removeFill: (fillId: number) => Promise<void>;
  savePlan: (body: TradePlanBody) => Promise<void>;
  removePlan: (stockCode: string) => Promise<void>;
  loadTrade: (stockCode: string) => Promise<void>;
  clearTrade: () => void;
};

function message(error: unknown): string {
  return error instanceof Error ? error.message : "알 수 없는 오류가 발생했습니다.";
}

export const usePostmortemStore = create<PostmortemState>((set, get) => ({
  fills: [],
  plans: [],
  patterns: null,
  trade: null,
  loading: false,
  error: null,

  loadOverview: async () => {
    set({ loading: true, error: null });
    try {
      const [fills, plans, patterns] = await Promise.all([
        listFills(),
        listTradePlans(),
        getPatterns(),
      ]);
      set({ fills: fills.items, plans: plans.items, patterns, loading: false });
    } catch (error) {
      set({ loading: false, error: message(error) });
    }
  },

  addFill: async (body) => {
    // 폼(ManualFillSection)이 성공 시 입력을 비운다 — 실패는 throw 로 알려 필드를 보존한다.
    // 체결 추가는 fills·패턴 집계만 바꾼다(계획 불변) → 그 둘만 다시 읽는다(removeFill 과 대칭).
    await createFill(body);
    const [fills, patterns] = await Promise.all([listFills(), getPatterns()]);
    set({ fills: fills.items, patterns });
  },

  removeFill: async (fillId) => {
    // onClick 에서 void 로 호출 — 실패 시 unhandled rejection 대신 error 로 표면화.
    try {
      await deleteFill(fillId);
      set({ fills: get().fills.filter((f) => f.id !== fillId) });
      // 패턴 집계가 바뀌므로 갱신.
      const patterns = await getPatterns();
      set({ patterns });
    } catch (error) {
      set({ error: message(error) });
    }
  },

  savePlan: async (body) => {
    // 폼(PlanSection)이 성공 시 입력을 비운다 — 실패는 throw 로 알려 필드를 보존한다.
    await upsertTradePlan(body);
    const plans = await listTradePlans();
    set({ plans: plans.items });
  },

  removePlan: async (stockCode) => {
    // onClick 에서 void 로 호출 — 실패 시 error 로 표면화.
    try {
      await deleteTradePlan(stockCode);
      set({ plans: get().plans.filter((p) => p.stock_code !== stockCode) });
    } catch (error) {
      set({ error: message(error) });
    }
  },

  loadTrade: async (stockCode) => {
    set({ loading: true, error: null, trade: null });
    try {
      const trade = await getTradePostmortem(stockCode);
      set({ trade, loading: false });
    } catch (error) {
      set({ loading: false, error: message(error) });
    }
  },

  clearTrade: () => set({ trade: null }),
}));
