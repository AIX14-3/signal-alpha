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
      <div className="overflow-hidden rounded-[20px] bg-navy px-7 py-6">
        <h1 className="text-[22px] font-extrabold tracking-tight text-white">
          커뮤니티 <span className="font-mono text-[13px] font-semibold text-sky">/ signals</span>
        </h1>
        <p className="mt-1.5 text-[12.5px] text-muted">
          데이터 방향성 기록(저널)을 공유하고 서로의 근거를 살펴보세요.
        </p>

        <div className="mt-[18px] flex gap-5" data-flow="community-sort">
          {SORTS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => void load(key)}
              className={`border-b-2 pb-2 text-[12.5px] font-semibold ${
                sort === key
                  ? "border-sky text-white"
                  : "border-transparent text-muted hover:text-white"
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      <div className="mt-5">
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
      </div>

      <p className="mt-10 text-center text-[12px] text-muted">{notice}</p>
    </div>
  );
}
