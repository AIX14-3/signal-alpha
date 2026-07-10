"use client";

import { create } from "zustand";
import {
  createJournal,
  deleteJournal,
  listJournals,
  updateJournal,
  type Journal,
} from "@/lib/apiClient";

type JournalState = {
  items: Journal[];
  count: number;
  loading: boolean;
  error: string | null;
  load: (params?: { stock_code?: string; limit?: number }) => Promise<void>;
  create: (body: {
    stock_code: string;
    final_signal_id: number;
    user_view: string;
    memo?: string;
    tags?: string[];
  }) => Promise<Journal>;
  update: (
    id: number,
    body: {
      user_view?: string;
      memo?: string;
      tags?: string[];
      retrospective_memo?: string;
      retro_outcome_class?: string;
    },
  ) => Promise<Journal>;
  remove: (id: number) => Promise<void>;
};

// 저널은 로그인 회원 누구나 이용 — 구독 게이트 없음(비로그인만 401). 소비 컴포넌트는 user 로만 분기.
export const useJournalStore = create<JournalState>((set, get) => ({
  items: [],
  count: 0,
  loading: false,
  error: null,

  async load(params) {
    set({ loading: true, error: null });
    try {
      const data = await listJournals({ limit: 50, ...params });
      set({ items: data.items, count: data.count, loading: false });
    } catch (error) {
      set({ loading: false, error: (error as Error).message });
    }
  },

  async create(body) {
    const created = await createJournal(body);
    await get().load();
    return created;
  },

  async update(id, body) {
    const updated = await updateJournal(id, body);
    set({ items: get().items.map((item) => (item.journal_id === id ? updated : item)) });
    return updated;
  },

  async remove(id) {
    await deleteJournal(id);
    set({
      items: get().items.filter((item) => item.journal_id !== id),
      count: Math.max(0, get().count - 1),
    });
  },
}));
