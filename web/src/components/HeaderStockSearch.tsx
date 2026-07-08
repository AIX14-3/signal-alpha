"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { searchStocks, type Stock } from "@/lib/apiClient";
import { useToastStore } from "@/stores/toastStore";

// 상단 헤더 전역 종목 검색. 검색은 "그 종목을 보러 간다"는 명시적 의도이므로 위치와 무관하게
// 종목 리포트(/report/{code})로 이동한다(검색→리포트 흐름, web-frontend-spec 페이지 인벤토리).
// (홈 리스트를 훑다 클릭하는 browse 는 LiveAnalysisSection 인라인 아코디언이 담당 — 의도 분리.)
export function HeaderStockSearch() {
  const router = useRouter();
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
      router.push(`/report/${code}`);
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
