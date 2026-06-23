"use client";

import { useState } from "react";
import { addWatchlist, ApiError, removeWatchlist } from "@/lib/apiClient";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";

export function WatchlistButton({ stockCode }: { stockCode: string }) {
  const user = useAuthStore((state) => state.user);
  const showToast = useToastStore((state) => state.show);
  const [added, setAdded] = useState(false);
  const [busy, setBusy] = useState(false);

  async function toggle() {
    if (!user) {
      showToast("로그인이 필요합니다.", "error");
      return;
    }
    setBusy(true);
    try {
      if (added) {
        await removeWatchlist(stockCode);
        setAdded(false);
        showToast("관심종목에서 제거했습니다.");
      } else {
        await addWatchlist(stockCode);
        setAdded(true);
        showToast("관심종목에 추가했습니다.", "success");
      }
    } catch (error) {
      // 한도 초과(WATCHLIST_LIMIT_EXCEEDED) 등 백엔드 안내 메시지를 토스트로 표시.
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
      className="rounded-full border border-line px-4 py-2 text-[13.5px] font-semibold text-navy-soft hover:border-navy hover:text-navy disabled:opacity-60"
    >
      {added ? "✓ 관심종목" : "＋ 관심종목"}
    </button>
  );
}
