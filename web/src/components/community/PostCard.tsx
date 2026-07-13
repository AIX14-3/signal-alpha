"use client";

import Link from "next/link";
import { StockLogo } from "@/components/StockLogo";
import type { CommunityPost } from "@/lib/apiClient";
import { authorLabel, formatDate } from "./util";

// 피드 목록의 게시글 카드(게시판 행 형식): [글번호] [회사 로고·이름] | [제목 + 작성자·날짜] [🔥 · 반응].
// 카드 자체에 테두리가 있으므로 내부는 박스를 겹치지 않고 소프트 칩·구분선으로만 영역을 나눈다.
export function PostCard({ post }: { post: CommunityPost }) {
  const j = post.journal;

  return (
    <Link
      href={`/community/${post.id}`}
      className="flex items-center gap-4 rounded-[16px] border border-line bg-surface px-5 py-3.5 shadow-[0_1px_2px_rgba(15,27,51,.04)] transition hover:-translate-y-[2px] hover:border-[rgba(139,92,246,0.4)] hover:shadow-[0_10px_28px_rgba(15,27,51,.10)]"
      data-flow="community-post-card"
    >
      {/* 글번호 — 본문과 같은 서체(Pretendard). 정렬만 tabular-nums 로 고정 폭. */}
      <span className="w-[44px] shrink-0 text-center text-[14px] font-bold tabular-nums text-muted">
        {post.id}
      </span>

      {/* 회사 로고 + 이름 — 인라인 */}
      {j?.stock ? (
        <div className="flex w-[132px] shrink-0 items-center gap-2.5">
          <StockLogo code={j.stock.ticker} name={j.stock.name} size={36} />
          <div className="min-w-0">
            <div className="truncate text-[13px] font-bold leading-tight text-navy">
              {j.stock.name ?? j.stock.ticker}
            </div>
            <div className="mt-0.5 truncate text-[10.5px] text-muted">
              {j.stock.ticker}
            </div>
          </div>
        </div>
      ) : (
        <div className="w-[132px] shrink-0" aria-hidden />
      )}

      {/* 구분선 */}
      <div className="h-9 w-px shrink-0 bg-line" aria-hidden />

      {/* 제목 + 작성자·날짜 */}
      <div className="min-w-0 flex-1">
        {/* 제목 안의 사이점(·)도 제거 — 앞뒤 공백을 한 칸으로 정리한다. */}
        <h3 className="truncate text-[15px] font-extrabold leading-snug text-navy">
          {post.title.replace(/\s*·\s*/g, " ")}
        </h3>
        <div className="mt-1.5 flex items-center gap-2 text-[11px] text-muted">
          <span className="font-semibold text-navy-soft">{authorLabel(post.author)}</span>
          <span>{formatDate(post.created_at)}</span>
        </div>
      </div>

      {/* 우측: 인기 점수 + 반응 */}
      <div className="flex shrink-0 flex-col items-end gap-1.5">
        {post.ranking_score != null && (
          <span
            className="rounded-full px-2 py-0.5 text-[10.5px] font-extrabold"
            style={{ background: "rgba(249,115,22,.12)", color: "#ea580c" }}
          >
            🔥 {Math.round(post.ranking_score)}
          </span>
        )}
        <div className="flex items-center gap-3 text-[11px] text-muted">
          <span>♥ {post.like_count}</span>
          <span>💬 {post.comment_count}</span>
          <span>👁 {post.view_count}</span>
        </div>
      </div>
    </Link>
  );
}
