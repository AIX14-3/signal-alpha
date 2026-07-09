"use client";

import Link from "next/link";
import type { CommunityPost } from "@/lib/apiClient";
import { authorLabel, formatDate, postDirection } from "./util";

const DIRECTION_STYLE = {
  long: {
    grad: "linear-gradient(160deg,#ecfdf5,#d1fae5)",
    ticker: "#065f46",
    tag: "#10b981",
    value: "#059669",
  },
  short: {
    grad: "linear-gradient(160deg,#fef2f2,#fee2e2)",
    ticker: "#991b1b",
    tag: "#ef4444",
    value: "#dc2626",
  },
  neutral: {
    grad: "linear-gradient(160deg,#f5f3ff,#ede9fe)",
    ticker: "#5b21b6",
    tag: "#8a97ab",
    value: "#36425c",
  },
} as const;

// 피드 목록의 게시글 카드. 상세로 링크. 저널 라이브 참조가 있으면 좌측에 종목 시그널 블록,
// show_pnl 이면 수익률% 를 함께 노출(가격·절대손익은 응답에 아예 없음).
export function PostCard({ post }: { post: CommunityPost }) {
  const j = post.journal;
  const dir = postDirection(post);
  const style = DIRECTION_STYLE[dir.kind];

  return (
    <Link
      href={`/community/${post.id}`}
      className="flex overflow-hidden rounded-[16px] border border-line bg-surface shadow-[0_1px_2px_rgba(15,27,51,.04)] transition hover:-translate-y-[2px] hover:shadow-[0_10px_28px_rgba(15,27,51,.10)]"
      data-flow="community-post-card"
    >
      {j?.stock && (
        <div
          className="flex w-[96px] shrink-0 flex-col justify-between border-r border-line px-3 py-[15px]"
          style={{ background: style.grad }}
        >
          <div>
            <div className="font-mono text-[12px] font-bold" style={{ color: style.ticker }}>
              {j.stock.ticker}
            </div>
            <div className="mt-0.5 text-[11px] font-bold text-navy">
              {j.stock.name ?? j.stock.ticker}
            </div>
          </div>
          <div>
            <div
              className="text-[9.5px] font-bold uppercase tracking-wide"
              style={{ color: style.tag }}
            >
              {dir.icon} {dir.label}
            </div>
            {dir.pctText ? (
              <div
                className="mt-0.5 text-[18px] font-extrabold leading-tight tabular-nums"
                style={{ color: style.value }}
              >
                {dir.pctText}
              </div>
            ) : (
              <div className="mt-[3px] text-[12.5px] font-bold leading-tight text-navy-soft">
                관망
              </div>
            )}
          </div>
        </div>
      )}

      <div className="min-w-0 flex-1 px-4 py-[15px]">
        <div className="flex items-start justify-between gap-2.5">
          <h3 className="text-[14.5px] font-extrabold leading-snug">{post.title}</h3>
          {post.ranking_score != null && (
            <span className="shrink-0 text-[10.5px] font-extrabold text-sky-deep">
              🔥{Math.round(post.ranking_score)}
            </span>
          )}
        </div>

        {post.body && (
          <p className="mt-2 line-clamp-2 text-[12.5px] leading-[1.5] text-navy-soft">
            {post.body}
          </p>
        )}

        <div className="mt-[11px] flex items-center gap-2.5 text-[11px] text-muted">
          <span className="font-semibold text-navy-soft">{authorLabel(post.author)}</span>
          <span>{formatDate(post.created_at)}</span>
          <span className="ml-auto flex items-center gap-[11px]">
            <span>♥ {post.like_count}</span>
            <span>💬 {post.comment_count}</span>
            <span>👁 {post.view_count}</span>
          </span>
        </div>
      </div>
    </Link>
  );
}
