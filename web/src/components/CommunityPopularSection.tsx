"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { listPopular, type CommunityPost } from "@/lib/apiClient";

// 우③ 커뮤니티 인기순위 — 워커 배치 가중합(weekly) 상위 게시글 노출(FR-8, auth none).
// 홈 전용의 가벼운 로컬 상태(커뮤니티 페이지 스토어와 분리).
const TOP_N = 5;

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
    <section data-section="community-popular" className="glass-card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[14px] font-bold text-navy">커뮤니티 인기순위</h2>
        <Link href="/community" className="text-[12px] font-semibold text-sky-deep hover:underline">
          더 보기 →
        </Link>
      </div>

      {loading ? (
        <ul className="animate-pulse space-y-2">
          {[0, 1, 2].map((i) => (
            <li key={i} className="h-9 rounded-[10px] bg-surface-2" />
          ))}
        </ul>
      ) : error || items.length === 0 ? (
        <p className="px-1 py-6 text-center text-[12.5px] text-muted">
          인기 게시글이 아직 없습니다.
        </p>
      ) : (
        <ol className="space-y-1">
          {items.map((post, i) => (
            <li key={post.id}>
              <Link
                href={`/community/${post.id}`}
                className="flex items-center gap-2.5 rounded-[10px] px-2 py-2 transition hover:bg-surface-2"
              >
                <span
                  className={`grid h-5 w-5 shrink-0 place-items-center rounded-full text-[11px] font-bold ${
                    i < 3 ? "brand-grad text-white" : "bg-surface-2 text-muted"
                  }`}
                >
                  {i + 1}
                </span>
                <span className="min-w-0 flex-1 truncate text-[13px] text-navy-soft">
                  {post.title}
                </span>
                <span className="shrink-0 text-[11px] text-muted">♡ {post.like_count}</span>
              </Link>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
