"use client";

import { useEffect, useState } from "react";
import { ApiError } from "@/lib/apiClient";
import { useAuthStore } from "@/stores/authStore";
import { useToastStore } from "@/stores/toastStore";
import { useWatchlistStore } from "@/stores/watchlistStore";

// 담김 여부는 로컬 state 가 아니라 watchlistStore(서버 목록)에서 파생한다.
// 관심종목에서 들어온 리포트는 처음부터 "✓ 관심종목"으로 보여야 하고,
// 토글 결과가 스트립·관심종목 페이지·마이페이지에 즉시 반영돼야 하기 때문.
export function WatchlistButton({ stockCode }: { stockCode: string }) {
  const user = useAuthStore((state) => state.user);
  const showToast = useToastStore((state) => state.show);
  const items = useWatchlistStore((s) => s.items);
  const loading = useWatchlistStore((s) => s.loading);
  const loaded = useWatchlistStore((s) => s.loaded);
  const ensureLoaded = useWatchlistStore((s) => s.ensureLoaded);
  const add = useWatchlistStore((s) => s.add);
  const remove = useWatchlistStore((s) => s.remove);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (user) void ensureLoaded();
  }, [user, ensureLoaded]);

  const added = items.some((w) => w.stock.stock_code === stockCode);
  // 비로그인은 즉시 토스트로 안내해야 하므로 대기 상태로 잠그지 않는다.
  const pending = Boolean(user) && !loaded && loading;

  async function toggle() {
    if (!user) {
      showToast("로그인이 필요합니다.", "error");
      return;
    }
    setBusy(true);
    try {
      if (added) {
        await remove(stockCode);
        showToast("관심종목에서 제거했습니다.");
      } else {
        await add(stockCode);
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
      disabled={busy || pending}
      aria-pressed={added}
      data-added={added ? "true" : "false"}
      className={`rounded-full border px-4 py-2 text-[13.5px] font-semibold transition disabled:opacity-60 ${
        added
          ? "border-navy bg-navy text-white hover:opacity-90"
          : "border-line text-navy-soft hover:border-navy hover:text-navy"
      }`}
    >
      {pending ? "…" : added ? "✓ 관심종목" : "＋ 관심종목"}
    </button>
  );
}
