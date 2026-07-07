"use client";

// 매매 부검 스토어 — 브로커 연동·체결·계획·단건/패턴 부검. 전 기능 구독 전용(402)이라
// 게이트는 소비 컴포넌트가 user.subscription_active 로 선분기(journalStore 관례).

import { create } from "zustand";
import {
  type BrokerConnectBody,
  type BrokerCredential,
  type PatternSummary,
  type TradeFill,
  type TradePlan,
  type TradePlanBody,
  type TradePostmortem,
  connectBroker,
  deleteTradePlan,
  disconnectBroker,
  getPatterns,
  getTradePostmortem,
  listBrokers,
  listFills,
  listTradePlans,
  requestSync,
  upsertTradePlan,
} from "@/lib/apiClient";

type PostmortemState = {
  brokers: BrokerCredential[];
  plans: TradePlan[];
  fills: TradeFill[];
  patterns: PatternSummary | null;
  trade: TradePostmortem | null;
  loading: boolean;
  error: string | null;
  syncMessage: string | null;

  loadOverview: () => Promise<void>;
  connect: (body: BrokerConnectBody) => Promise<void>;
  disconnect: (credentialId: number) => Promise<void>;
  sync: () => Promise<void>;
  savePlan: (body: TradePlanBody) => Promise<void>;
  removePlan: (stockCode: string) => Promise<void>;
  loadTrade: (stockCode: string) => Promise<void>;
  clearTrade: () => void;
};

function message(error: unknown): string {
  return error instanceof Error ? error.message : "알 수 없는 오류가 발생했습니다.";
}

export const usePostmortemStore = create<PostmortemState>((set, get) => ({
  brokers: [],
  plans: [],
  fills: [],
  patterns: null,
  trade: null,
  loading: false,
  error: null,
  syncMessage: null,

  loadOverview: async () => {
    set({ loading: true, error: null });
    try {
      const [brokers, plans, fills, patterns] = await Promise.all([
        listBrokers(),
        listTradePlans(),
        listFills(),
        getPatterns(),
      ]);
      set({
        brokers: brokers.items,
        plans: plans.items,
        fills: fills.items,
        patterns,
        loading: false,
      });
    } catch (error) {
      set({ loading: false, error: message(error) });
    }
  },

  connect: async (body) => {
    await connectBroker(body);
    const brokers = await listBrokers();
    set({ brokers: brokers.items });
  },

  disconnect: async (credentialId) => {
    await disconnectBroker(credentialId);
    set({ brokers: get().brokers.filter((b) => b.id !== credentialId) });
  },

  sync: async () => {
    set({ syncMessage: null, error: null });
    try {
      const result = await requestSync();
      set({
        syncMessage: `동기화를 요청했습니다(${result.requested}건). 잠시 후 체결이 반영됩니다.`,
      });
    } catch (error) {
      set({ error: message(error) });
    }
  },

  savePlan: async (body) => {
    await upsertTradePlan(body);
    const plans = await listTradePlans();
    set({ plans: plans.items });
  },

  removePlan: async (stockCode) => {
    await deleteTradePlan(stockCode);
    set({ plans: get().plans.filter((p) => p.stock_code !== stockCode) });
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
