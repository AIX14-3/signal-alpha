"use client";

import Link from "next/link";
import { useEffect } from "react";
import { StockLogo } from "@/components/StockLogo";
import { useAuthStore } from "@/stores/authStore";
import { useHomeStore } from "@/stores/homeStore";
import { useWatchlistStore } from "@/stores/watchlistStore";

// 관심종목 — 대시보드 상단 가로 밴드. 로그인 시 내 워치리스트, 비로그인 시 로그인 유도(NFR-1).
// 항목 클릭은 "실시간 분석 종목" 아코디언을 그 종목으로 연다(select).
export function WatchlistSection() {
  const user = useAuthStore((s) => s.user);
  const items = useWatchlistStore((s) => s.items);
  const loading = useWatchlistStore((s) => s.loading);
  const load = useWatchlistStore((s) => s.load);
  const select = useHomeStore((s) => s.select);
  const selectedCode = useHomeStore((s) => s.selectedCode);

  useEffect(() => {
    if (user) void load();
  }, [user, load]);

  return (
    <section data-section="watchlist" className="glass-card p-4">
      <div className="mb-3 flex items-center justify-between">
        <h2 className="text-[14px] font-bold text-navy">관심종목</h2>
        {user && (
          <Link href="/mypage" className="text-[12px] font-semibold text-sky-deep hover:underline">
            관리 →
          </Link>
        )}
      </div>

      {!user ? (
        <div
          data-flow="watchlist-login-guide"
          className="grid place-items-center gap-2 rounded-[12px] bg-surface-2/60 px-4 py-8 text-center"
        >
          <p className="text-[13px] text-navy-soft">로그인 후 관심종목을 등록하세요</p>
          <Link
            href="/login"
            className="brand-grad rounded-full px-4 py-2 text-[13px] font-bold text-white hover:opacity-90"
          >
            로그인
          </Link>
        </div>
      ) : loading ? (
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <div key={i} className="h-[52px] animate-pulse rounded-[12px] bg-surface-2" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <p className="px-1 py-6 text-center text-[12.5px] text-muted">
          아직 등록한 관심종목이 없습니다.
        </p>
      ) : (
        <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
          {items.map((w) => (
            <button
              key={w.stock.stock_code}
              type="button"
              onClick={() => void select(w.stock.stock_code)}
              className={`flex min-w-0 items-center gap-2.5 rounded-[12px] border px-3.5 py-2.5 text-left transition hover:bg-surface-2 ${
                selectedCode === w.stock.stock_code ? "border-sky bg-surface-2" : "border-line"
              }`}
            >
              <StockLogo code={w.stock.stock_code} name={w.stock.stock_name} size={32} />
              <span className="flex min-w-0 flex-col">
                <span className="truncate text-[13.5px] font-semibold text-navy-soft">
                  {w.stock.stock_name ?? w.stock.stock_code}
                </span>
                <span className="text-[11px] text-muted">{w.stock.stock_code}</span>
              </span>
            </button>
          ))}
        </div>
      )}
    </section>
  );
}
