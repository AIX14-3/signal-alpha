"use client";

import Link from "next/link";
import { useEffect } from "react";
import type { ReactNode } from "react";
import { GuardGate } from "@/components/GuardGate";
import { HeaderStockSearch } from "@/components/HeaderStockSearch";
import { Toaster } from "@/components/Toaster";
import { WatchlistStrip } from "@/components/WatchlistStrip";
import { useAuthStore } from "@/stores/authStore";

// 상단 메뉴는 홈·브리핑·커뮤니티·관심종목·마이(+로그인) — 모두 동일한 텍스트 링크 스타일이다.
// 관심종목은 최상위 /watchlist 페이지로, 매매 부검·방법론은 마이 안으로, 요금제는 폐지한다.
const NAV = [
  { href: "/", label: "홈" },
  { href: "/brief", label: "브리핑" },
  { href: "/community", label: "커뮤니티" },
  { href: "/watchlist", label: "관심종목" },
  { href: "/mypage", label: "마이" },
];

export function AppShell({ children }: { children: ReactNode }) {
  const user = useAuthStore((state) => state.user);
  const status = useAuthStore((state) => state.status);
  const hydrate = useAuthStore((state) => state.hydrate);
  const logout = useAuthStore((state) => state.logout);

  useEffect(() => {
    if (status === "idle") {
      void hydrate();
    }
  }, [status, hydrate]);

  return (
    <div className="relative flex min-h-screen flex-col">
      <div className="bg-static" aria-hidden="true" />
      <nav className="sticky top-0 z-50 border-b border-line bg-bg/70 backdrop-blur-md">
        <div className="mx-auto flex h-16 max-w-[1080px] items-center gap-4 px-6">
          <Link href="/" className="flex shrink-0 items-center gap-2 text-[18px] font-bold">
            <span className="brand-grad grid h-7 w-7 place-items-center rounded-[8px] text-[15px] font-extrabold text-white">
              α
            </span>
            Signal <span className="font-semibold text-muted">알파</span>
          </Link>

          {/* 종목 검색 — 좌 pane 에서 헤더로 승격. 데스크톱 표시(모바일은 좁아 숨김). */}
          <div className="hidden min-w-0 flex-1 md:block">
            <div className="mx-auto max-w-[380px]">
              <HeaderStockSearch />
            </div>
          </div>

          <div className="ml-auto flex shrink-0 items-center gap-6">
            {NAV.map((item) => (
              <Link
                key={item.label}
                href={item.href}
                className="text-[14.5px] font-medium text-navy-soft hover:text-navy"
              >
                {item.label}
              </Link>
            ))}
            {user ? (
              <button
                type="button"
                onClick={() => void logout()}
                className="rounded-full border border-line px-4 py-2 text-[13.5px] font-semibold text-navy-soft hover:border-navy hover:text-navy"
              >
                {user.nickname ?? user.member_code} · 로그아웃
              </button>
            ) : (
              <Link
                href="/login"
                className="brand-grad rounded-full px-5 py-[10px] text-[14.5px] font-bold text-white hover:opacity-90"
              >
                로그인
              </Link>
            )}
          </div>
        </div>
      </nav>

      <WatchlistStrip />

      <main className="relative z-10 mx-auto w-full max-w-[1080px] flex-1 px-6">
        <GuardGate>{children}</GuardGate>
      </main>

      <footer className="relative z-10 border-t border-line py-8 text-center text-[13px] text-muted">
        Signal α · 데이터 방향성과 근거를 제공하는 서비스 (투자 권유·수익 보장 아님)
      </footer>

      <Toaster />
    </div>
  );
}
