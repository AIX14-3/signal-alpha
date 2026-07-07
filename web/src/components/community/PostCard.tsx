"use client";

import Link from "next/link";
import type { CommunityPost } from "@/lib/apiClient";
import { authorLabel, formatDate, viewLabel } from "./util";

// 피드 목록의 게시글 카드. 상세로 링크. 저널 라이브 참조가 있으면 종목/판단 요약,
// show_pnl 이면 수익률% pill 을 함께 노출(가격·절대손익은 응답에 아예 없음).
export function PostCard({ post }: { post: CommunityPost }) {
  const j = post.journal;
  const pnl = j?.pnl_pct;
  const hasPnl = post.show_pnl && pnl != null;

  return (
    <Link
      href={`/community/${post.id}`}
      className="card block px-5 py-4 transition hover:border-sky"
      data-flow="community-post-card"
    >
      <div className="flex items-start justify-between gap-3">
        <h3 className="font-bold leading-snug">{post.title}</h3>
        {post.ranking_score != null && (
          <span className="pill flat shrink-0" style={{ padding: "3px 9px", fontSize: 12 }}>
            🔥 {Math.round(post.ranking_score)}
          </span>
        )}
      </div>

      {j && (
        <div className="mt-2 flex flex-wrap items-center gap-2 text-[12.5px]">
          {j.stock && (
            <span className="pill flat" style={{ padding: "3px 9px" }}>
              {j.stock.name ?? j.stock.ticker}
              <span className="font-normal text-muted"> {j.stock.ticker}</span>
            </span>
          )}
          {j.user_view && (
            <span className="text-muted">{viewLabel(j.user_view)}</span>
          )}
          {hasPnl && (
            <span
              className={`pill ${pnl >= 0 ? "up" : "down"}`}
              style={{ padding: "3px 9px" }}
            >
              {pnl >= 0 ? "+" : ""}
              {pnl.toFixed(2)}%
            </span>
          )}
        </div>
      )}

      {post.body && (
        <p className="mt-2 line-clamp-2 text-[13.5px] text-navy-soft">{post.body}</p>
      )}

      <div className="mt-3 flex items-center gap-3 text-[12px] text-muted">
        <span>{authorLabel(post.author)}</span>
        <span>·</span>
        <span>{formatDate(post.created_at)}</span>
        <span className="ml-auto flex items-center gap-3">
          <span>♥ {post.like_count}</span>
          <span>💬 {post.comment_count}</span>
          <span>👁 {post.view_count}</span>
        </span>
      </div>
    </Link>
  );
}
