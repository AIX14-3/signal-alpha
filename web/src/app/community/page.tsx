"use client";

import { useEffect } from "react";
import { PostCard } from "@/components/community/PostCard";
import { useCommunityStore, type CommunitySort } from "@/stores/communityStore";

const SORTS: [CommunitySort, string][] = [
  ["latest", "최신"],
  ["weekly", "주간 인기"],
  ["all", "인기"],
];

export default function CommunityPage() {
  const { items, sort, loading, loadingMore, error, hasMore, notice, load, loadMore } =
    useCommunityStore();

  useEffect(() => {
    void load("latest");
  }, [load]);

  return (
    <div className="py-10" data-page="community">
      <header className="mb-6 flex items-end justify-between gap-4">
        <div>
          <h1 className="text-[24px] font-bold">커뮤니티</h1>
          <p className="mt-1 text-[13.5px] text-muted">
            데이터 방향성 기록(저널)을 공유하고 서로의 근거를 살펴보세요.
          </p>
        </div>
      </header>

      <div className="mb-4 flex gap-2" data-flow="community-sort">
        {SORTS.map(([key, label]) => (
          <button
            key={key}
            type="button"
            onClick={() => void load(key)}
            className={`pill flat text-[13px] ${
              sort === key ? "!border-sky !text-sky-deep font-bold" : ""
            }`}
            style={{ padding: "5px 14px", border: "1px solid var(--color-line)" }}
          >
            {label}
          </button>
        ))}
      </div>

      {loading ? (
        <p className="py-16 text-center text-muted">불러오는 중…</p>
      ) : error ? (
        <p className="py-16 text-center text-red">{error}</p>
      ) : items.length === 0 ? (
        <p className="py-16 text-center text-muted">아직 공유된 게시글이 없습니다.</p>
      ) : (
        <>
          <ul className="space-y-3">
            {items.map((post) => (
              <li key={post.id}>
                <PostCard post={post} />
              </li>
            ))}
          </ul>

          {hasMore && (
            <div className="mt-6 text-center">
              <button
                type="button"
                onClick={() => void loadMore()}
                disabled={loadingMore}
                className="rounded-full border border-line px-6 py-2.5 text-[13.5px] font-semibold text-navy-soft hover:border-navy hover:text-navy disabled:opacity-60"
              >
                {loadingMore ? "불러오는 중…" : "더 보기"}
              </button>
            </div>
          )}
        </>
      )}

      <p className="mt-10 text-center text-[12px] text-muted">{notice}</p>
    </div>
  );
}
