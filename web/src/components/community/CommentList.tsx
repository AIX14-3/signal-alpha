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
import { authorLabel, formatDate } from "./util";

const PAGE = 20;

// 게시글 댓글 트리(1단계 대댓글까지). 목록 조회·작성·대댓글·삭제(본인)·신고·좋아요.
// 본인 판별은 author.member_code === user.member_code(응답에 user id 없음).
export function CommentList({ postId }: { postId: number }) {
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
      <h2 className="mb-3 font-bold">댓글 {comments.length}</h2>

      <CommentForm onSubmit={(b) => submit(b, null)} placeholder="댓글을 입력하세요" />

      {loading ? (
        <p className="mt-4 text-[13.5px] text-muted">불러오는 중…</p>
      ) : roots.length === 0 ? (
        <p className="mt-4 text-[13.5px] text-muted">첫 댓글을 남겨보세요.</p>
      ) : (
        <ul className="mt-4 space-y-4">
          {roots.map((c) => (
            <li key={c.id}>
              <CommentItem
                comment={c}
                mine={!!user && c.author.member_code === user.member_code}
                onReply={() => setReplyTo(replyTo === c.id ? null : c.id)}
                onDelete={() => void remove(c.id)}
              />
              {replyTo === c.id && (
                <div className="mt-2 pl-6">
                  <CommentForm
                    onSubmit={(b) => submit(b, c.id)}
                    placeholder="답글을 입력하세요"
                    autoFocus
                  />
                </div>
              )}
              {repliesOf(c.id).length > 0 && (
                <ul className="mt-2 space-y-3 border-l border-line pl-6">
                  {repliesOf(c.id).map((r) => (
                    <li key={r.id}>
                      <CommentItem
                        comment={r}
                        mine={!!user && r.author.member_code === user.member_code}
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
  onReply,
  onDelete,
}: {
  comment: CommunityComment;
  mine: boolean;
  onReply?: () => void;
  onDelete: () => void;
}) {
  return (
    <div>
      <div className="flex items-center gap-2 text-[12px] text-muted">
        <span className="font-semibold text-navy-soft">{authorLabel(comment.author)}</span>
        <span>{formatDate(comment.created_at)}</span>
      </div>
      <p className="mt-1 whitespace-pre-wrap text-[14px]">{comment.body}</p>
      <div className="mt-1.5 flex items-center gap-3">
        <ReactionButton
          target="comment"
          targetId={comment.id}
          count={comment.like_count}
          active={comment.my_reactions.like}
        />
        {onReply && (
          <button
            type="button"
            onClick={onReply}
            className="text-[12.5px] font-semibold text-muted hover:text-sky-deep"
          >
            답글
          </button>
        )}
        {mine ? (
          <button
            type="button"
            onClick={onDelete}
            className="text-[12.5px] font-semibold text-muted hover:text-red"
          >
            삭제
          </button>
        ) : (
          <ReportButton target="comment" targetId={comment.id} />
        )}
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
