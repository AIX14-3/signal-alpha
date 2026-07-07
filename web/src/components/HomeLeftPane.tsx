"use client";

import { useState } from "react";
import { searchStocks, type Stock } from "@/lib/apiClient";
import { dateTimeKST } from "@/lib/format";
import { useHomeStore } from "@/stores/homeStore";

// FR-2/FR-3 좌 pane 하이브리드 = 상단 검색/종목 칩 + 하단 전역 뉴스 피드(#798).
// 종목 칩 또는 뉴스 항목을 고르면 우 pane(선택 종목 상세)이 갱신된다.
export function HomeLeftPane() {
  const feed = useHomeStore((s) => s.feed);
  const chips = useHomeStore((s) => s.chips);
  const loading = useHomeStore((s) => s.loading);
  const selectedCode = useHomeStore((s) => s.selectedCode);
  const select = useHomeStore((s) => s.select);

  return (
    <aside className="flex min-h-0 flex-col gap-4">
      <StockSearch />

      {chips.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {chips.slice(0, 12).map((s) => (
            <button
              key={s.stock_code}
              type="button"
              onClick={() => void select(s.stock_code)}
              className={`pill flat text-[12.5px] ${selectedCode === s.stock_code ? "!border-sky !text-sky-deep font-bold" : ""}`}
              style={{ padding: "4px 11px" }}
            >
              {s.stock_name}
            </button>
          ))}
        </div>
      )}

      <div className="card min-h-0 flex-1 overflow-hidden">
        <div className="border-b border-line px-4 py-3 text-[13px] font-bold text-navy">
          실시간 뉴스 피드
        </div>
        {loading ? (
          <ul className="animate-pulse space-y-3 p-4">
            {[0, 1, 2, 3, 4].map((i) => (
              <li key={i} className="h-10 rounded-[8px] bg-surface-2" />
            ))}
          </ul>
        ) : feed.length === 0 ? (
          <p className="p-6 text-center text-[13px] text-muted">
            아직 수집된 뉴스가 없습니다.
          </p>
        ) : (
          <ul className="max-h-[560px] divide-y divide-line overflow-y-auto">
            {feed.map((n, i) => (
              <li key={`${n.stock_code}-${i}`}>
                <button
                  type="button"
                  onClick={() => n.stock_code && void select(n.stock_code)}
                  className={`block w-full px-4 py-3 text-left transition hover:bg-surface-2 ${selectedCode === n.stock_code ? "bg-surface-2" : ""}`}
                >
                  <div className="flex items-center gap-2 text-[12px] text-muted">
                    <span className="font-semibold text-sky-deep">{n.stock_name ?? n.stock_code}</span>
                    {n.press && <span>· {n.press}</span>}
                    {n.published_at && <span className="ml-auto shrink-0">{dateTimeKST(n.published_at).slice(5, 16)}</span>}
                  </div>
                  <div className="mt-1 line-clamp-2 text-[13.5px] text-navy-soft">{n.title}</div>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </aside>
  );
}

/** 검색어와 가장 잘 맞는 종목을 골라 우 pane 선택으로 연결(코드 정확일치 > 종목명 정확일치 > 첫 결과). */
function StockSearch() {
  const select = useHomeStore((s) => s.select);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function run() {
    const clean = query.trim();
    if (!clean) return;
    setBusy(true);
    setError(null);
    try {
      const data = await searchStocks(clean);
      if (data.items.length === 0) {
        setError("검색 결과가 없습니다.");
        return;
      }
      void select(pickBest(data.items, clean).stock_code);
      setQuery("");
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void run();
        }}
        className="flex items-center gap-2 rounded-full border border-line bg-surface py-1.5 pl-4 pr-1.5 shadow-[var(--shadow-card)] transition focus-within:border-sky focus-within:ring-4 focus-within:ring-sky/15"
      >
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              void run();
            }
          }}
          placeholder="종목명 또는 코드 검색"
          aria-label="종목 검색"
          className="min-w-0 flex-1 bg-transparent text-[14.5px] outline-none placeholder:text-muted"
        />
        <button
          type="submit"
          disabled={busy}
          className="shrink-0 rounded-full bg-navy px-4 py-2 text-[13px] font-semibold text-white transition hover:-translate-y-px disabled:opacity-60"
        >
          {busy ? "…" : "검색"}
        </button>
      </form>
      {error && <p className="mt-1.5 px-2 text-[12.5px] text-muted">{error}</p>}
    </div>
  );
}

function pickBest(items: Stock[], term: string): Stock {
  const clean = term.trim().toLowerCase();
  return (
    items.find((s) => s.stock_code.toLowerCase() === clean) ??
    items.find((s) => s.stock_name.toLowerCase() === clean) ??
    items[0]
  );
}
