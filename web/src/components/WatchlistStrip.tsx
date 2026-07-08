"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { listWatchlists, type WatchlistItem } from "@/lib/apiClient";
import { useAuthStore } from "@/stores/authStore";

// 관심종목 스트립 — 네비게이션 바로 아래에 내 관심종목을 칩으로 노출(전 페이지 공통).
// 로그인(관심종목 有) 시에만 표시. 각 칩은 해당 종목 리포트로 이동.
export function WatchlistStrip() {
  const status = useAuthStore((s) => s.status);
  const [items, setItems] = useState<WatchlistItem[]>([]);

  useEffect(() => {
    if (status !== "authenticated") {
      setItems([]);
      return;
    }
    let alive = true;
    listWatchlists()
      .then((d) => alive && setItems(d.items))
      .catch(() => alive && setItems([]));
    return () => {
      alive = false;
    };
  }, [status]);

  if (status !== "authenticated" || items.length === 0) return null;

  return (
    <div className="sticky top-16 z-40 border-b border-line bg-bg/60 backdrop-blur-md" data-section="watchlist-strip">
      <div className="mx-auto flex max-w-[1320px] items-center gap-2 overflow-x-auto px-6 py-2">
        <span className="shrink-0 text-[12px] font-semibold text-muted">관심종목</span>
        {items.map((w) => (
          <Link
            key={w.stock.stock_code}
            href={`/report/${encodeURIComponent(w.stock.stock_code)}`}
            className="shrink-0 rounded-full border border-line px-3 py-1 text-[12.5px] font-medium text-navy-soft hover:border-navy hover:text-navy"
          >
            {w.stock.stock_name} <span className="text-muted">{w.stock.stock_code}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
