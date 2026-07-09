"use client";

import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useCallback, useEffect, useState } from "react";
import { CommentList } from "@/components/community/CommentList";
import { ReactionButton } from "@/components/community/ReactionButton";
import { ReportButton } from "@/components/community/ReportButton";
import {
  authorLabel,
  formatDate,
  postDirection,
  viewLabel,
} from "@/components/community/util";
import {
  ApiError,
  deletePost,
  getPost,
  updatePost,
  type CommunityPost,
} from "@/lib/apiClient";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";

const DIRECTION_STYLE = {
  long: {
    grad: "linear-gradient(160deg,rgba(16,185,129,.22),rgba(16,185,129,.08))",
    border: "rgba(16,185,129,.35)",
    ticker: "#6ee7b7",
    tag: "#34d399",
    value: "#6ee7b7",
  },
  short: {
    grad: "linear-gradient(160deg,rgba(239,68,68,.22),rgba(239,68,68,.08))",
    border: "rgba(239,68,68,.35)",
    ticker: "#fca5a5",
    tag: "#f87171",
    value: "#fca5a5",
  },
  neutral: {
    grad: "linear-gradient(160deg,rgba(139,92,246,.22),rgba(139,92,246,.08))",
    border: "rgba(139,92,246,.35)",
    ticker: "#c4b5fd",
    tag: "#a78bfa",
    value: "#c4b5fd",
  },
} as const;

export default function CommunityPostPage() {
  const params = useParams<{ postId: string }>();
  const postId = Number(params.postId);
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const status = useAuthStore((s) => s.status);
  const showToast = useToastStore((s) => s.show);

  const [post, setPost] = useState<CommunityPost | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState(false);

  const reload = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setPost(await getPost(postId));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "게시글을 불러오지 못했습니다.");
    } finally {
      setLoading(false);
    }
  }, [postId]);

  const handleCommentCountChange = useCallback((delta: number) => {
    setPost((p) => (p ? { ...p, comment_count: Math.max(0, p.comment_count + delta) } : p));
  }, []);

  useEffect(() => {
    // 로그인 시 조회수 1회 기록되므로 auth hydrate 완료 후 로드(비로그인도 열람 가능).
    if (Number.isNaN(postId)) return;
    if (status === "idle" || status === "loading") return;
    void reload();
  }, [postId, status, reload]);

  if (loading) return <p className="py-16 text-center text-muted">불러오는 중…</p>;
  if (error) return <p className="py-16 text-center text-red">{error}</p>;
  if (!post) return null;

  const mine = !!user && post.author.member_code === user.member_code;
  const j = post.journal;
  const dir = postDirection(post);
  const style = DIRECTION_STYLE[dir.kind];

  async function remove() {
    if (!window.confirm("게시글을 삭제할까요?")) return;
    try {
      await deletePost(postId);
      showToast("게시글을 삭제했습니다.", "success");
      router.push("/community");
    } catch (e) {
      showToast(e instanceof ApiError ? e.message : "삭제에 실패했습니다.", "error");
    }
  }

  return (
    <div className="py-10" data-page="community-detail">
      {editing ? (
        <>
          <Link
            href="/community"
            className="text-[13px] font-semibold text-muted hover:text-navy"
          >
            ← 커뮤니티
          </Link>
          <EditForm
            post={post}
            onCancel={() => setEditing(false)}
            onSaved={(updated) => {
              setPost(updated);
              setEditing(false);
              showToast("게시글을 수정했습니다.", "success");
            }}
          />
        </>
      ) : (
        <article
          className="overflow-hidden rounded-[22px] border border-line shadow-[var(--shadow-card)]"
          data-panel="community-post"
        >
          <div className="bg-navy px-7 py-6">
            <div className="flex items-center justify-between gap-3">
              <Link
                href="/community"
                className="text-[12.5px] font-bold text-muted hover:text-white"
              >
                ← 커뮤니티
              </Link>
              {mine && (
                <div className="flex shrink-0 gap-3">
                  <button
                    type="button"
                    onClick={() => setEditing(true)}
                    className="text-[12.5px] font-semibold text-muted hover:text-white"
                  >
                    수정
                  </button>
                  <button
                    type="button"
                    onClick={() => void remove()}
                    className="text-[12.5px] font-semibold text-muted hover:text-red"
                  >
                    삭제
                  </button>
                </div>
              )}
            </div>

            <div className="mt-4 flex items-stretch gap-3.5">
              {j?.stock && (
                <div
                  className="w-[112px] shrink-0 rounded-[14px] border px-3.5 py-3"
                  style={{ background: style.grad, borderColor: style.border }}
                >
                  <div
                    className="font-mono text-[13px] font-bold"
                    style={{ color: style.ticker }}
                  >
                    {j.stock.ticker}
                  </div>
                  <div className="mt-0.5 text-[12.5px] font-bold text-white">
                    {j.stock.name ?? j.stock.ticker}
                  </div>
                  <div
                    className="mt-3 text-[10px] font-bold uppercase tracking-wide"
                    style={{ color: style.tag }}
                  >
                    {dir.icon} {dir.label}
                  </div>
                  {dir.pctText ? (
                    <div
                      className="text-[20px] font-extrabold leading-tight tabular-nums"
                      style={{ color: style.value }}
                    >
                      {dir.pctText}
                    </div>
                  ) : (
                    <div className="text-[13px] font-bold text-muted">관망</div>
                  )}
                </div>
              )}
              <div className="flex min-w-0 flex-1 flex-col justify-center">
                <h1 className="text-[20px] font-extrabold leading-snug text-white">
                  {post.title}
                </h1>
                <div className="mt-2.5 flex items-center gap-2.5 text-[11.5px] text-muted">
                  <span className="font-bold text-[#c4b5fd]">{authorLabel(post.author)}</span>
                  <span>{formatDate(post.created_at)}</span>
                  <span>👁 {post.view_count}</span>
                </div>
              </div>
            </div>
          </div>

          <div className="bg-surface px-7 py-6">
            {/* 라이브 참조 저널 요약(화이트리스트). 원본 삭제 시 안내만. */}
            {j ? (
              <div className="rounded-[14px] border border-line bg-surface-2 px-4 py-3.5 text-[13px]">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-[10px] font-extrabold uppercase tracking-wide text-sky-deep">
                    ＄ 참조 저널
                  </span>
                  {j.stock ? (
                    <Link
                      href={`/report/${j.stock.ticker}`}
                      className="font-bold hover:text-sky-deep"
                    >
                      {j.stock.name ?? j.stock.ticker}
                      <span className="font-normal text-muted"> {j.stock.ticker}</span>
                    </Link>
                  ) : (
                    <span className="font-bold">종목 정보 없음</span>
                  )}
                  {j.user_view && <span className="text-muted">· {viewLabel(j.user_view)}</span>}
                  {dir.pctText && (
                    <span
                      className={`pill ${dir.kind === "long" ? "up" : "down"}`}
                      style={{ padding: "3px 9px" }}
                    >
                      {dir.pctText}
                    </span>
                  )}
                </div>
                {j.user_memo && (
                  <p className="mt-2.5 whitespace-pre-wrap text-navy-soft">{j.user_memo}</p>
                )}
              </div>
            ) : (
              post.journal === null && (
                <p className="text-[12.5px] text-muted">참조한 저널 원본이 없습니다.</p>
              )
            )}

            {post.body && (
              <p className="mt-4 whitespace-pre-wrap text-[14.5px] leading-[1.75]">
                {post.body}
              </p>
            )}

            <div className="mt-5 flex items-center gap-2.5 border-t border-line pt-4">
              <ReactionButton
                variant="pill"
                target="post"
                targetId={post.id}
                count={post.like_count}
                active={post.my_reactions.like}
                onCount={(likeCount) => setPost((p) => (p ? { ...p, like_count: likeCount } : p))}
              />
              <ReactionButton
                variant="pill"
                target="post"
                targetId={post.id}
                type="bookmark"
                count={post.bookmark_count}
                active={post.my_reactions.bookmark}
                onCount={(bookmarkCount) =>
                  setPost((p) => (p ? { ...p, bookmark_count: bookmarkCount } : p))
                }
              />
              <span className="text-[13px] text-muted">💬 {post.comment_count}</span>
              {!mine && (
                <span className="ml-auto">
                  <ReportButton target="post" targetId={post.id} />
                </span>
              )}
            </div>
          </div>
        </article>
      )}

      <CommentList
        postId={post.id}
        postAuthorCode={post.author.member_code}
        onCountChange={handleCommentCountChange}
      />
    </div>
  );
}

function EditForm({
  post,
  onCancel,
  onSaved,
}: {
  post: CommunityPost;
  onCancel: () => void;
  onSaved: (updated: CommunityPost) => void;
}) {
  const showToast = useToastStore((s) => s.show);
  const [title, setTitle] = useState(post.title);
  const [body, setBody] = useState(post.body);
  const [showPnl, setShowPnl] = useState(post.show_pnl);
  const [busy, setBusy] = useState(false);

  async function save() {
    if (!title.trim()) {
      showToast("제목을 입력하세요.", "error");
      return;
    }
    setBusy(true);
    try {
      const updated = await updatePost(post.id, { title: title.trim(), body, show_pnl: showPnl });
      onSaved(updated);
    } catch (e) {
      showToast(e instanceof ApiError ? e.message : "수정에 실패했습니다.", "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card mt-3 space-y-3 px-6 py-5" data-panel="community-post-edit">
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="제목"
        className="card w-full px-4 py-2.5 text-[15px] font-semibold outline-none focus:border-sky"
      />
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        rows={5}
        placeholder="내용"
        className="card w-full px-4 py-2.5 text-[14px] outline-none focus:border-sky"
      />
      {post.journal && (
        <button
          type="button"
          onClick={() => setShowPnl((v) => !v)}
          className={`pill flat text-[12.5px] ${showPnl ? "!border-sky !text-sky-deep font-bold" : ""}`}
          style={{ padding: "4px 12px", border: "1px solid var(--color-line)" }}
        >
          수익률% 공개 {showPnl ? "ON" : "OFF"}
        </button>
      )}
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => void save()}
          disabled={busy}
          className="brand-grad rounded-full px-6 py-2.5 text-[13.5px] font-bold text-white disabled:opacity-50"
        >
          저장
        </button>
        <button
          type="button"
          onClick={onCancel}
          className="rounded-full border border-line px-6 py-2.5 text-[13.5px] font-semibold text-navy-soft hover:border-navy"
        >
          취소
        </button>
      </div>
    </div>
  );
}
