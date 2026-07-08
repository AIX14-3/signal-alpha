"use client";

import Link from "next/link";
import { useEffect } from "react";
import { useAuthStore } from "@/stores/authStore";
import { useHomeStore } from "@/stores/homeStore";
import { useWatchlistStore } from "@/stores/watchlistStore";

// 우① 관심종목 — 로그인 시 내 워치리스트, 비로그인 시 로그인 유도(NFR-1).
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
        <ul className="animate-pulse space-y-2">
          {[0, 1, 2].map((i) => (
            <li key={i} className="h-10 rounded-[10px] bg-surface-2" />
          ))}
        </ul>
      ) : items.length === 0 ? (
        <p className="px-1 py-6 text-center text-[12.5px] text-muted">
          아직 등록한 관심종목이 없습니다.
        </p>
      ) : (
        <ul className="space-y-1">
          {items.map((w) => (
            <li key={w.stock.stock_code}>
              <button
                type="button"
                onClick={() => void select(w.stock.stock_code)}
                className={`flex w-full items-center justify-between rounded-[10px] px-3 py-2.5 text-left transition hover:bg-surface-2 ${
                  selectedCode === w.stock.stock_code ? "bg-surface-2" : ""
                }`}
              >
                <span className="min-w-0 truncate text-[13.5px] font-semibold text-navy-soft">
                  {w.stock.stock_name ?? w.stock.stock_code}
                </span>
                <span className="shrink-0 text-[11.5px] text-muted">{w.stock.stock_code}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
