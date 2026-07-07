"use client";

// 매매 부검 스토어 — 브로커 연동·체결·계획·단건/패턴 부검. 전 기능 구독 전용(402)이라
// 게이트는 소비 컴포넌트가 user.subscription_active 로 선분기(journalStore 관례).

import { create } from "zustand";
import {
  type BrokerConnectBody,
  type BrokerCredential,
  type PatternSummary,
  type TradePlan,
  type TradePlanBody,
  type TradePostmortem,
  connectBroker,
  deleteTradePlan,
  disconnectBroker,
  getPatterns,
  getTradePostmortem,
  listBrokers,
  listTradePlans,
  requestSync,
  upsertTradePlan,
} from "@/lib/apiClient";

type PostmortemState = {
  brokers: BrokerCredential[];
  plans: TradePlan[];
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
  patterns: null,
  trade: null,
  loading: false,
  error: null,
  syncMessage: null,

  loadOverview: async () => {
    set({ loading: true, error: null });
    try {
      const [brokers, plans, patterns] = await Promise.all([
        listBrokers(),
        listTradePlans(),
        getPatterns(),
      ]);
      set({ brokers: brokers.items, plans: plans.items, patterns, loading: false });
    } catch (error) {
      set({ loading: false, error: message(error) });
    }
  },

  connect: async (body) => {
    // 폼(BrokerConnectForm)이 자체 try/catch 로 에러를 표시하므로 여기선 throw 를 전파한다.
    await connectBroker(body);
    const brokers = await listBrokers();
    set({ brokers: brokers.items });
  },

  disconnect: async (credentialId) => {
    // onClick 에서 void 로 호출 — 실패 시 unhandled rejection 대신 error 로 표면화.
    try {
      await disconnectBroker(credentialId);
      set({ brokers: get().brokers.filter((b) => b.id !== credentialId) });
    } catch (error) {
      set({ error: message(error) });
    }
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
