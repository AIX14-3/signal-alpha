"use client";

import { useEffect, useState } from "react";
import {
  ApiError,
  toggleCommentReaction,
  togglePostReaction,
  type ReactionType,
} from "@/lib/apiClient";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";

// 좋아요 토글(게시글/댓글). 로그인 필요. 본인 글 좋아요는 백엔드가 400 SELF_REACTION.
// onCount 로 갱신된 like_count 를 부모에 반영한다.
export function ReactionButton({
  target,
  targetId,
  count,
  active: initialActive = false,
  onCount,
  type = "like",
  variant = "pill",
}: {
  target: "post" | "comment";
  targetId: number;
  // 알려진 좋아요 수. 댓글 목록은 카운트를 안 주므로 생략 가능(토글 후 표시).
  count?: number;
  active?: boolean;
  onCount?: (likeCount: number) => void;
  type?: ReactionType;
  // pill: 게시글 상세의 큰 버튼. inline: 댓글 행의 텍스트형 버튼.
  variant?: "pill" | "inline";
}) {
  const user = useAuthStore((state) => state.user);
  const showToast = useToastStore((state) => state.show);
  const [active, setActive] = useState(initialActive);
  const [busy, setBusy] = useState(false);
  const [localCount, setLocalCount] = useState<number | undefined>(count);

  useEffect(() => {
    setActive(initialActive);
  }, [initialActive]);

  useEffect(() => {
    setLocalCount(count);
  }, [count]);

  async function toggle() {
    if (!user) {
      showToast("로그인이 필요합니다.", "error");
      return;
    }
    setBusy(true);
    try {
      const result =
        target === "post"
          ? await togglePostReaction(targetId, type)
          : await toggleCommentReaction(targetId, type);
      const nextCount = type === "bookmark" ? result.bookmark_count : result.like_count;
      setActive(result.active);
      setLocalCount(nextCount);
      onCount?.(nextCount);
    } catch (error) {
      showToast(
        error instanceof ApiError ? error.message : "처리 중 오류가 발생했습니다.",
        "error",
      );
    } finally {
      setBusy(false);
    }
  }

  const icon = type === "bookmark" ? "🔖" : "♥";
  const label =
    type === "bookmark" ? (active ? "저장됨" : "저장") : active ? "좋아요 취소" : "좋아요";

  if (variant === "inline") {
    return (
      <button
        type="button"
        onClick={() => void toggle()}
        disabled={busy}
        data-flow={`community-${type}`}
        className={`text-[11.5px] font-semibold disabled:opacity-60 ${
          active ? "text-sky-deep" : "text-muted hover:text-sky-deep"
        }`}
      >
        {icon} {localCount ?? 0}
      </button>
    );
  }

  return (
    <button
      type="button"
      onClick={() => void toggle()}
      disabled={busy}
      data-flow={`community-${type}`}
      className={`rounded-full px-4 py-2 text-[13px] font-bold disabled:opacity-60 ${
        active
          ? type === "like"
            ? "brand-grad text-white shadow-[0_6px_14px_rgba(124,58,237,.25)]"
            : "border border-line bg-white text-sky-deep"
          : "border border-line text-navy-soft hover:border-navy hover:text-navy"
      }`}
    >
      {icon} {label}
      {localCount != null ? ` ${localCount}` : ""}
    </button>
  );
}
