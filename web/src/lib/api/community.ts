// 커뮤니티 게시판 — 저널 공유 피드/상세·댓글·반응·신고. main-server /api/community 계약과 1:1.
// 공개 응답은 화이트리스트 필드만(가격/절대손익/PII 없음). 수익률%는 show_pnl=true 게시글만.

import { apiFetch } from "./core";

export type CommunityAuthor = {
  member_code: string | null;
  nickname: string | null;
};

// 게시글이 라이브 참조하는 저널 스냅샷(공개 화이트리스트). 원본 삭제 시 journal=null.
export type CommunityPostJournal = {
  user_view: string | null;
  user_memo: string | null;
  stock: { ticker: string; name: string | null } | null;
  // show_pnl=true 인 게시글에서만 채워진다(그 외 null). 절대금액·가격은 항상 부재.
  pnl_pct: number | null;
};

export type CommunityPost = {
  id: number;
  title: string;
  body: string;
  author: CommunityAuthor;
  journal: CommunityPostJournal | null;
  show_pnl: boolean;
  view_count: number;
  like_count: number;
  comment_count: number;
  created_at: string | null;
  updated_at: string | null;
  // /popular 응답에만 존재(워커 배치 가중합 점수).
  ranking_score?: number;
};

export type CommunityComment = {
  id: number;
  post_id: number;
  parent_comment_id: number | null;
  body: string;
  author: CommunityAuthor;
  created_at: string | null;
};

export type CommunityFeed = {
  items: CommunityPost[];
  // 커서 페이지네이션 — 다음 페이지 요청에 그대로 cursor 로 넘긴다. null 이면 마지막 페이지.
  next_cursor: number | null;
  notice: string;
};

export type CommunityPopular = {
  items: CommunityPost[];
  next_cursor: string | null;
  notice: string;
};

export type ReactionType = "like" | "bookmark";

export type ReactionResult = {
  action: "added" | "removed";
  type: ReactionType;
  like_count: number;
};

export type ReportResult = { reported: boolean; hidden: boolean };

// 최신 피드(키셋 커서, id 내림차순). cursor 를 주면 그 id 미만을 반환.
export async function listPosts(
  params: { cursor?: number | null; limit?: number } = {},
): Promise<CommunityFeed> {
  const search = new URLSearchParams();
  if (params.cursor != null) search.set("cursor", String(params.cursor));
  if (params.limit) search.set("limit", String(params.limit));
  const qs = search.toString();
  return apiFetch(`/api/community/posts${qs ? `?${qs}` : ""}`, { auth: "none" });
}

// 인기 랭킹 피드(워커 배치 가중합). window=weekly(롤링 7일)|all, score:id 커서 페이지네이션.
export async function listPopular(
  params: { window?: "weekly" | "all"; limit?: number; cursor?: string | null } = {},
): Promise<CommunityPopular> {
  const search = new URLSearchParams();
  if (params.window) search.set("window", params.window);
  if (params.limit) search.set("limit", String(params.limit));
  if (params.cursor != null) search.set("cursor", params.cursor);
  const qs = search.toString();
  return apiFetch(`/api/community/popular${qs ? `?${qs}` : ""}`, { auth: "none" });
}

export async function getPost(id: number): Promise<CommunityPost> {
  // 로그인 시 조회 1회 기록(당일 1회). 비로그인도 열람 가능하므로 실패해도 읽기는 된다.
  return apiFetch(`/api/community/posts/${id}`);
}

export async function createPost(body: {
  title: string;
  body?: string;
  journal_id?: number | null;
  show_pnl?: boolean;
}): Promise<CommunityPost> {
  return apiFetch("/api/community/posts", { method: "POST", body: JSON.stringify(body) });
}

export async function updatePost(
  id: number,
  body: { title?: string; body?: string; show_pnl?: boolean },
): Promise<CommunityPost> {
  return apiFetch(`/api/community/posts/${id}`, { method: "PATCH", body: JSON.stringify(body) });
}

export async function deletePost(id: number): Promise<{ deleted: boolean }> {
  return apiFetch(`/api/community/posts/${id}`, { method: "DELETE" });
}

export async function listComments(postId: number): Promise<{ items: CommunityComment[] }> {
  return apiFetch(`/api/community/posts/${postId}/comments`, { auth: "none" });
}

export async function createComment(
  postId: number,
  body: { body: string; parent_comment_id?: number | null },
): Promise<CommunityComment> {
  return apiFetch(`/api/community/posts/${postId}/comments`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export async function deleteComment(commentId: number): Promise<{ deleted: boolean }> {
  return apiFetch(`/api/community/comments/${commentId}`, { method: "DELETE" });
}

export async function togglePostReaction(
  postId: number,
  type: ReactionType,
): Promise<ReactionResult> {
  return apiFetch(`/api/community/posts/${postId}/reactions`, {
    method: "POST",
    body: JSON.stringify({ type }),
  });
}

export async function toggleCommentReaction(
  commentId: number,
  type: ReactionType,
): Promise<ReactionResult> {
  return apiFetch(`/api/community/comments/${commentId}/reactions`, {
    method: "POST",
    body: JSON.stringify({ type }),
  });
}

export async function reportPost(postId: number, reason?: string): Promise<ReportResult> {
  return apiFetch(`/api/community/posts/${postId}/report`, {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? null }),
  });
}

export async function reportComment(commentId: number, reason?: string): Promise<ReportResult> {
  return apiFetch(`/api/community/comments/${commentId}/report`, {
    method: "POST",
    body: JSON.stringify({ reason: reason ?? null }),
  });
}
