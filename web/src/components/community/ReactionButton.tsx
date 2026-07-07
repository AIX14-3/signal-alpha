"use client";

import { useState } from "react";
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
}: {
  target: "post" | "comment";
  targetId: number;
  // 알려진 좋아요 수. 댓글 목록은 카운트를 안 주므로 생략 가능(토글 후 표시).
  count?: number;
  active?: boolean;
  onCount?: (likeCount: number) => void;
  type?: ReactionType;
}) {
  const user = useAuthStore((state) => state.user);
  const showToast = useToastStore((state) => state.show);
  const [active, setActive] = useState(initialActive);
  const [busy, setBusy] = useState(false);
  const [localCount, setLocalCount] = useState<number | undefined>(count);

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
      setActive(result.action === "added");
      setLocalCount(result.like_count);
      onCount?.(result.like_count);
    } catch (error) {
      showToast(
        error instanceof ApiError ? error.message : "처리 중 오류가 발생했습니다.",
        "error",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <button
      type="button"
      onClick={() => void toggle()}
      disabled={busy}
      data-flow="community-like"
      className={`rounded-full border px-4 py-2 text-[13.5px] font-semibold disabled:opacity-60 ${
        active
          ? "border-sky bg-surface-2 text-sky-deep"
          : "border-line text-navy-soft hover:border-navy hover:text-navy"
      }`}
    >
      {active ? "♥" : "♡"}
      {localCount != null ? ` ${localCount}` : ""}
    </button>
  );
}
