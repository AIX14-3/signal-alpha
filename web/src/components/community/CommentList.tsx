"use client";

import { useCallback, useEffect, useState } from "react";
import {
  ApiError,
  createComment,
  deleteComment,
  listComments,
  type CommunityComment,
} from "@/lib/apiClient";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";
import { ReactionButton } from "./ReactionButton";
import { ReportButton } from "./ReportButton";
import { authorLabel, avatarGradient, avatarInitial, formatDate } from "./util";

const PAGE = 20;

// 게시글 댓글 트리(1단계 대댓글까지). 목록 조회·작성·대댓글·삭제(본인)·신고·좋아요.
// 본인 판별은 author.member_code === user.member_code(응답에 user id 없음).
export function CommentList({
  postId,
  postAuthorCode,
  onCountChange,
}: {
  postId: number;
  // 게시글 작성자 코드 — 댓글 작성자와 일치하면 "작성자" 배지 표시.
  postAuthorCode?: string | null;
  onCountChange?: (delta: number) => void;
}) {
  const user = useAuthStore((s) => s.user);
  const showToast = useToastStore((s) => s.show);
  const [comments, setComments] = useState<CommunityComment[]>([]);
  const [nextCursor, setNextCursor] = useState<number | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [replyTo, setReplyTo] = useState<number | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const data = await listComments(postId, { limit: PAGE, cursor: null });
      setComments(data.items);
      setNextCursor(data.next_cursor);
    } catch {
      // 목록 조회 실패는 조용히 — 빈 목록으로 둔다(작성 시 재시도).
      setNextCursor(null);
    } finally {
      setLoading(false);
    }
  }, [postId]);

  useEffect(() => {
    void reload();
  }, [reload]);

  async function submit(body: string, parentId: number | null): Promise<boolean> {
    if (!user) {
      showToast("로그인이 필요합니다.", "error");
      return false;
    }
    const text = body.trim();
    if (!text) return false;
    try {
      await createComment(postId, { body: text, parent_comment_id: parentId });
      setReplyTo(null);
      await reload();
      onCountChange?.(1);
      return true;
    } catch (error) {
      showToast(
        error instanceof ApiError ? error.message : "댓글 작성에 실패했습니다.",
        "error",
      );
      return false;
    }
  }

  async function remove(id: number) {
    if (!window.confirm("댓글을 삭제할까요?")) return;
    try {
      await deleteComment(id);
      await reload();
      onCountChange?.(-1);
      showToast("댓글을 삭제했습니다.", "success");
    } catch (error) {
      showToast(
        error instanceof ApiError ? error.message : "삭제에 실패했습니다.",
        "error",
      );
    }
  }

  async function loadMore() {
    if (nextCursor == null || loadingMore) return;
    setLoadingMore(true);
    try {
      const data = await listComments(postId, { limit: PAGE, cursor: nextCursor });
      setComments((prev) => [...prev, ...data.items]);
      setNextCursor(data.next_cursor);
    } catch (error) {
      showToast(
        error instanceof ApiError ? error.message : "댓글을 더 불러오지 못했습니다.",
        "error",
      );
    } finally {
      setLoadingMore(false);
    }
  }

  const roots = comments.filter((c) => c.parent_comment_id == null);
  const repliesOf = (id: number) => comments.filter((c) => c.parent_comment_id === id);

  return (
    <section className="mt-8" data-panel="community-comments">
      <h2 className="mb-3 text-[15px] font-extrabold">
        댓글 <span className="text-sky-deep">{comments.length}</span>
      </h2>

      <CommentForm onSubmit={(b) => submit(b, null)} placeholder="댓글을 입력하세요" />

      {loading ? (
        <p className="mt-4 text-[13.5px] text-muted">불러오는 중…</p>
      ) : roots.length === 0 ? (
        <p className="mt-4 text-[13.5px] text-muted">첫 댓글을 남겨보세요.</p>
      ) : (
        <ul className="mt-5 space-y-[18px]">
          {roots.map((c) => (
            <li key={c.id}>
              <CommentItem
                comment={c}
                mine={!!user && c.author.member_code === user.member_code}
                isPostAuthor={!!postAuthorCode && c.author.member_code === postAuthorCode}
                onReply={() => setReplyTo(replyTo === c.id ? null : c.id)}
                onDelete={() => void remove(c.id)}
              />
              {replyTo === c.id && (
                <div className="mt-2 pl-[45px]">
                  <CommentForm
                    onSubmit={(b) => submit(b, c.id)}
                    placeholder="답글을 입력하세요"
                    autoFocus
                  />
                </div>
              )}
              {repliesOf(c.id).length > 0 && (
                <ul className="mt-3.5 space-y-3.5 pl-[24px]">
                  {repliesOf(c.id).map((r) => (
                    <li key={r.id} className="border-l-2 border-[#ede9fe] pl-3.5">
                      <CommentItem
                        comment={r}
                        mine={!!user && r.author.member_code === user.member_code}
                        isPostAuthor={!!postAuthorCode && r.author.member_code === postAuthorCode}
                        onDelete={() => void remove(r.id)}
                      />
                    </li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ul>
      )}
      {nextCursor != null && !loading && (
        <button
          type="button"
          onClick={() => void loadMore()}
          disabled={loadingMore}
          className="mt-4 rounded-full border border-line px-4 py-2 text-[13px] font-bold text-navy-soft hover:border-sky hover:text-sky-deep disabled:opacity-50"
        >
          {loadingMore ? "불러오는 중..." : "더 보기"}
        </button>
      )}
    </section>
  );
}

function CommentItem({
  comment,
  mine,
  isPostAuthor,
  onReply,
  onDelete,
}: {
  comment: CommunityComment;
  mine: boolean;
  isPostAuthor?: boolean;
  onReply?: () => void;
  onDelete: () => void;
}) {
  const label = authorLabel(comment.author);
  const seed = comment.author.member_code ?? label;

  return (
    <div className="flex gap-2.5">
      <div
        className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-[11px] font-mono text-[12px] font-extrabold text-white"
        style={{ background: avatarGradient(seed) }}
      >
        {avatarInitial(label)}
      </div>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-2 text-[11.5px] text-muted">
          <span className="font-bold text-navy-soft">{label}</span>
          <span>{formatDate(comment.created_at)}</span>
          {isPostAuthor && (
            <span className="rounded-[5px] bg-sky/12 px-1.5 py-px text-[9px] font-extrabold text-sky-deep">
              작성자
            </span>
          )}
        </div>
        <p className="mt-1 whitespace-pre-wrap text-[13.5px] leading-[1.6]">{comment.body}</p>
        <div className="mt-1.5 flex items-center gap-3.5">
          <ReactionButton
            variant="inline"
            target="comment"
            targetId={comment.id}
            count={comment.like_count}
            active={comment.my_reactions.like}
          />
          <ReactionButton
            variant="inline"
            target="comment"
            targetId={comment.id}
            type="bookmark"
            count={comment.bookmark_count}
            active={comment.my_reactions.bookmark}
          />
          {onReply && (
            <button
              type="button"
              onClick={onReply}
              className="text-[11.5px] font-semibold text-muted hover:text-sky-deep"
            >
              답글
            </button>
          )}
          {mine ? (
            <button
              type="button"
              onClick={onDelete}
              className="text-[11.5px] font-semibold text-muted hover:text-red"
            >
              삭제
            </button>
          ) : (
            <ReportButton target="comment" targetId={comment.id} />
          )}
        </div>
      </div>
    </div>
  );
}

function CommentForm({
  onSubmit,
  placeholder,
  autoFocus = false,
}: {
  onSubmit: (body: string) => Promise<boolean>;
  placeholder: string;
  autoFocus?: boolean;
}) {
  const [body, setBody] = useState("");
  const [busy, setBusy] = useState(false);

  async function handle() {
    setBusy(true);
    const ok = await onSubmit(body);
    if (ok) setBody("");
    setBusy(false);
  }

  return (
    <div className="flex items-end gap-2">
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={2}
        autoFocus={autoFocus}
        placeholder={placeholder}
        className="card flex-1 px-4 py-2.5 text-[13.5px] outline-none focus:border-sky"
      />
      <button
        type="button"
        onClick={() => void handle()}
        disabled={busy || !body.trim()}
        className="brand-grad rounded-full px-5 py-2.5 text-[13.5px] font-bold text-white disabled:opacity-50"
      >
        등록
      </button>
    </div>
  );
}
