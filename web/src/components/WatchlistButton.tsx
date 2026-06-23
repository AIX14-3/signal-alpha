"use client";

import { useState } from "react";
import { addWatchlist, ApiError, removeWatchlist } from "@/lib/apiClient";
import { useAuthStore } from "@/stores/authStore";

export function WatchlistButton({ stockCode }: { stockCode: string }) {
  const user = useAuthStore((state) => state.user);
  const [added, setAdded] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function toggle() {
    if (!user) {
      setMessage("로그인이 필요합니다.");
      return;
    }
    setBusy(true);
    setMessage(null);
    try {
      if (added) {
        await removeWatchlist(stockCode);
        setAdded(false);
      } else {
        await addWatchlist(stockCode);
        setAdded(true);
      }
    } catch (error) {
      setMessage(error instanceof ApiError ? error.message : "처리 중 오류가 발생했습니다.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex items-center gap-3">
      <button
        type="button"
        onClick={() => void toggle()}
        disabled={busy}
        className="rounded-full border border-line px-4 py-2 text-[13.5px] font-semibold text-navy-soft hover:border-navy hover:text-navy disabled:opacity-60"
      >
        {added ? "✓ 관심종목" : "＋ 관심종목"}
      </button>
      {message && <span className="text-[12.5px] text-muted">{message}</span>}
    </div>
  );
}
