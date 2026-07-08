"use client";

import { usePathname, useRouter } from "next/navigation";
import { useState } from "react";
import { searchStocks, type Stock } from "@/lib/apiClient";
import { useHomeStore } from "@/stores/homeStore";
import { useToastStore } from "@/stores/toastStore";

// 상단 헤더 전역 종목 검색 — 홈 좌 pane 에서 헤더로 승격(#799 후속).
// 홈(2-pane)에서는 우 pane 선택(homeStore.select)으로 연결하고,
// 그 외 페이지에서는 종목 리포트(/report/{code})로 이동한다.
export function HeaderStockSearch() {
  const router = useRouter();
  const pathname = usePathname();
  const showToast = useToastStore((s) => s.show);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);

  async function run() {
    const clean = query.trim();
    if (!clean) return;
    setBusy(true);
    try {
      const data = await searchStocks(clean);
      if (data.items.length === 0) {
        showToast("검색 결과가 없습니다.", "error");
        return;
      }
      const code = pickBest(data.items, clean).stock_code;
      setQuery("");
      if (pathname === "/") void useHomeStore.getState().select(code);
      else router.push(`/report/${code}`);
    } catch (e) {
      showToast((e as Error).message, "error");
    } finally {
      setBusy(false);
    }
  }

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        void run();
      }}
      className="flex w-full items-center gap-2 rounded-full border border-line bg-surface py-1.5 pl-4 pr-1.5 shadow-[var(--shadow-card)] transition focus-within:border-sky focus-within:ring-4 focus-within:ring-sky/15"
    >
      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="종목명 또는 코드 검색"
        aria-label="종목 검색"
        className="min-w-0 flex-1 bg-transparent text-[14px] outline-none placeholder:text-muted"
      />
      <button
        type="submit"
        disabled={busy}
        className="shrink-0 rounded-full bg-navy px-4 py-1.5 text-[13px] font-semibold text-white transition hover:-translate-y-px disabled:opacity-60"
      >
        {busy ? "…" : "검색"}
      </button>
    </form>
  );
}

/** 검색어와 가장 잘 맞는 종목(코드 정확일치 > 종목명 정확일치 > 첫 결과). */
function pickBest(items: Stock[], term: string): Stock {
  const clean = term.trim().toLowerCase();
  return (
    items.find((s) => s.stock_code.toLowerCase() === clean) ??
    items.find((s) => s.stock_name.toLowerCase() === clean) ??
    items[0]
  );
}
