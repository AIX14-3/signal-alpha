"use client";

import { useState } from "react";
import { ApiError, reportComment, reportPost } from "@/lib/apiClient";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";

// 신고(게시글/댓글). 로그인 필요, 중복 신고는 백엔드 unique 로 1회만 집계. 임계 도달 시
// 대상이 자동 숨김(응답 hidden=true) → 안내만 표시.
export function ReportButton({
  target,
  targetId,
}: {
  target: "post" | "comment";
  targetId: number;
}) {
  const user = useAuthStore((state) => state.user);
  const showToast = useToastStore((state) => state.show);
  const [busy, setBusy] = useState(false);

  async function report() {
    if (!user) {
      showToast("로그인이 필요합니다.", "error");
      return;
    }
    if (!window.confirm("이 게시물을 신고할까요?")) return;
    setBusy(true);
    try {
      const result =
        target === "post" ? await reportPost(targetId) : await reportComment(targetId);
      showToast(
        result.hidden ? "신고가 접수되어 숨김 처리되었습니다." : "신고가 접수되었습니다.",
        "success",
      );
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
      onClick={() => void report()}
      disabled={busy}
      data-flow="community-report"
      className="text-[12.5px] font-semibold text-muted hover:text-red disabled:opacity-60"
    >
      신고
    </button>
  );
}
