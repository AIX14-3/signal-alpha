"use client";

import { create } from "zustand";
import { listPopular, listPosts, type CommunityPost } from "@/lib/apiClient";
import { NOTICE_FALLBACK } from "@/lib/format";

// 피드 정렬 3종(FR-6). latest=최신(키셋 커서), weekly/all=워커 배치 인기(offset).
export type CommunitySort = "latest" | "weekly" | "all";

const PAGE = 20;

type CommunityState = {
  items: CommunityPost[];
  sort: CommunitySort;
  loading: boolean; // 첫 페이지(정렬 전환 포함)
  loadingMore: boolean; // 더 보기
  error: string | null;
  hasMore: boolean;
  notice: string;
  // latest 커서(다음 페이지 id 상한) / popular offset — 정렬에 따라 하나만 쓴다.
  cursor: number | null;
  offset: number;
  load: (sort?: CommunitySort) => Promise<void>;
  loadMore: () => Promise<void>;
};

// 정렬별 한 페이지 조회 — items/다음 페이지 커서/hasMore 를 통일된 형태로 돌려준다.
async function fetchPage(
  sort: CommunitySort,
  opts: { cursor: number | null; offset: number },
): Promise<{ items: CommunityPost[]; nextCursor: number | null; hasMore: boolean; notice: string }> {
  if (sort === "latest") {
    const data = await listPosts({ limit: PAGE, cursor: opts.cursor });
    return {
      items: data.items,
      nextCursor: data.next_cursor,
      hasMore: data.next_cursor != null,
      notice: data.notice,
    };
  }
  const data = await listPopular({ window: sort, limit: PAGE, offset: opts.offset });
  return {
    items: data.items,
    nextCursor: null,
    // 인기는 커서가 없으니 "가득 찬 페이지면 더 있다"로 판단.
    hasMore: data.items.length === PAGE,
    notice: data.notice,
  };
}

export const useCommunityStore = create<CommunityState>((set, get) => ({
  items: [],
  sort: "latest",
  loading: false,
  loadingMore: false,
  error: null,
  hasMore: false,
  notice: NOTICE_FALLBACK,
  cursor: null,
  offset: 0,

  async load(sort) {
    const nextSort = sort ?? get().sort;
    set({ loading: true, error: null, sort: nextSort });
    try {
      const page = await fetchPage(nextSort, { cursor: null, offset: 0 });
      set({
        items: page.items,
        cursor: page.nextCursor,
        offset: page.items.length,
        hasMore: page.hasMore,
        notice: page.notice,
        loading: false,
      });
    } catch (error) {
      set({ loading: false, error: (error as Error).message });
    }
  },

  async loadMore() {
    const { loading, loadingMore, hasMore, sort, cursor, offset } = get();
    if (loading || loadingMore || !hasMore) return;
    set({ loadingMore: true, error: null });
    try {
      const page = await fetchPage(sort, { cursor, offset });
      set({
        items: [...get().items, ...page.items],
        cursor: page.nextCursor,
        offset: get().offset + page.items.length,
        hasMore: page.hasMore,
        loadingMore: false,
      });
    } catch (error) {
      set({ loadingMore: false, error: (error as Error).message });
    }
  },
}));
