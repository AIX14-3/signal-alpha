"use client";

import { create } from "zustand";
import { listPopular, listPosts, type CommunityPost } from "@/lib/apiClient";
import { NOTICE_FALLBACK } from "@/lib/format";

// 피드 정렬 3종(FR-6). latest=최신(id 커서), weekly/all=워커 배치 인기(score:id 커서).
export type CommunitySort = "latest" | "weekly" | "all";
type CommunityCursor = number | string | null;

const PAGE = 20;

type CommunityState = {
  items: CommunityPost[];
  sort: CommunitySort;
  loading: boolean; // 첫 페이지(정렬 전환 포함)
  loadingMore: boolean; // 더 보기
  error: string | null;
  hasMore: boolean;
  notice: string;
  // latest 는 number(id), popular 는 string(score:id) 커서.
  cursor: CommunityCursor;
  load: (sort?: CommunitySort) => Promise<void>;
  loadMore: () => Promise<void>;
};

// 정렬별 한 페이지 조회 — items/다음 페이지 커서/hasMore 를 통일된 형태로 돌려준다.
async function fetchPage(
  sort: CommunitySort,
  opts: { cursor: CommunityCursor },
): Promise<{ items: CommunityPost[]; nextCursor: CommunityCursor; hasMore: boolean; notice: string }> {
  if (sort === "latest") {
    const cursor = typeof opts.cursor === "number" ? opts.cursor : null;
    const data = await listPosts({ limit: PAGE, cursor });
    return {
      items: data.items,
      nextCursor: data.next_cursor,
      hasMore: data.next_cursor != null,
      notice: data.notice,
    };
  }
  const cursor = typeof opts.cursor === "string" ? opts.cursor : null;
  const data = await listPopular({ window: sort, limit: PAGE, cursor });
  return {
    items: data.items,
    nextCursor: data.next_cursor,
    hasMore: data.next_cursor != null,
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

  async load(sort) {
    const nextSort = sort ?? get().sort;
    set({ loading: true, error: null, sort: nextSort });
    try {
      const page = await fetchPage(nextSort, { cursor: null });
      set({
        items: page.items,
        cursor: page.nextCursor,
        hasMore: page.hasMore,
        notice: page.notice,
        loading: false,
      });
    } catch (error) {
      set({ loading: false, error: (error as Error).message });
    }
  },

  async loadMore() {
    const { loading, loadingMore, hasMore, sort, cursor } = get();
    if (loading || loadingMore || !hasMore) return;
    set({ loadingMore: true, error: null });
    try {
      const page = await fetchPage(sort, { cursor });
      set({
        items: [...get().items, ...page.items],
        cursor: page.nextCursor,
        hasMore: page.hasMore,
        loadingMore: false,
      });
    } catch (error) {
      set({ loadingMore: false, error: (error as Error).message });
    }
  },
}));
