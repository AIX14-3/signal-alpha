"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { listPopular, type CommunityPost } from "@/lib/apiClient";

// 커뮤니티 인기순위 — 워커 배치 가중합(weekly) 상위 게시글(FR-8, auth none).
// 홈 2컬럼(뉴스·분석) 아래 전체 폭 밴드로 배치되며, 좌우 폭을 채우기 위해 2열 그리드로 편다.
const TOP_N = 6;

export function CommunityPopularSection() {
  const [items, setItems] = useState<CommunityPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let alive = true;
    setLoading(true);
    listPopular({ window: "weekly", limit: TOP_N })
      .then((data) => {
        if (!alive) return;
        setItems(data.items);
        setLoading(false);
      })
      .catch((e) => {
        if (!alive) return;
        setError((e as Error).message);
        setLoading(false);
      });
    return () => {
      alive = false;
    };
  }, []);

  return (
    <section data-section="community-popular" className="glass-card p-4 sm:p-5">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[14px] font-bold text-navy">커뮤니티 인기순위</h2>
        <Link href="/community" className="text-[12px] font-semibold text-sky-deep hover:underline">
          더 보기 →
        </Link>
      </div>

      {loading ? (
        <div className="grid animate-pulse grid-cols-1 gap-2 sm:grid-cols-2">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-12 rounded-[10px] bg-surface-2" />
          ))}
        </div>
      ) : error || items.length === 0 ? (
        <p className="px-1 py-8 text-center text-[12.5px] text-muted">인기 게시글이 아직 없습니다.</p>
      ) : (
        <ol className="grid grid-cols-1 gap-x-8 gap-y-1.5 sm:grid-flow-col sm:grid-cols-2 sm:grid-rows-3">
          {items.slice(0, TOP_N).map((post, i) => {
            const stock = post.journal?.stock?.name;
            const author = post.author?.nickname;
            const meta = [author, stock].filter(Boolean).join(" ");
            return (
              <li key={post.id}>
                <Link
                  href={`/community/${post.id}`}
                  className="flex items-center gap-3 rounded-[12px] px-3 py-2.5 transition hover:bg-surface-2"
                >
                  <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full brand-grad text-[12px] font-bold text-white">
                    {i + 1}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate text-[13.5px] font-medium text-navy-soft">
                      {post.title}
                    </span>
                    {meta && <span className="block truncate text-[11.5px] text-muted">{meta}</span>}
                  </span>
                  <span className="flex shrink-0 items-center gap-2.5 text-[11.5px] text-muted">
                    <span>♡ {post.like_count}</span>
                    <span>💬 {post.comment_count}</span>
                  </span>
                </Link>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
