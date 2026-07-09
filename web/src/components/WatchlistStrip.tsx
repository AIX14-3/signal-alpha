"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getStockPrices, type WatchlistItem } from "@/lib/apiClient";
import { useAuthStore } from "@/stores/authStore";
import { useWatchlistStore } from "@/stores/watchlistStore";

// 등락률 = 최근 2개 일봉 종가로 계산(전용 시세 API 없음, 기존 /prices 재사용). 데이터 짧으면 null(칩엔 미표시).
type ChangePct = { pct: number } | null;

function useWatchlistChangePcts(items: WatchlistItem[]) {
  const [byCode, setByCode] = useState<Record<string, ChangePct>>({});

  useEffect(() => {
    if (items.length === 0) return;
    let alive = true;
    void Promise.all(
      items.map(async (w) => {
        try {
          const { series } = await getStockPrices(w.stock.stock_code, 14);
          const closes = series.map((p) => p.close).filter((c): c is number => c != null);
          if (closes.length < 2) return [w.stock.stock_code, null] as const;
          const [prev, last] = closes.slice(-2);
          return [w.stock.stock_code, { pct: ((last - prev) / prev) * 100 }] as const;
        } catch {
          return [w.stock.stock_code, null] as const;
        }
      }),
    ).then((entries) => {
      if (alive) setByCode(Object.fromEntries(entries));
    });
    return () => {
      alive = false;
    };
  }, [items]);

  return byCode;
}

// 관심종목 스트립 — 네비게이션 바로 아래에 내 관심종목을 칩으로 노출(전 페이지 공통).
// 로그인(관심종목 有) 시에만 표시. 각 칩은 해당 종목 리포트로 이동.
// 목록은 watchlistStore 공유 — 리포트의 관심종목 버튼 토글이 즉시 칩에 반영된다.
export function WatchlistStrip() {
  const status = useAuthStore((s) => s.status);
  const items = useWatchlistStore((s) => s.items);
  const ensureLoaded = useWatchlistStore((s) => s.ensureLoaded);
  const reset = useWatchlistStore((s) => s.reset);
  const changePcts = useWatchlistChangePcts(items);

  useEffect(() => {
    if (status === "authenticated") void ensureLoaded();
    else if (status === "anonymous") reset();
  }, [status, ensureLoaded, reset]);

  if (status !== "authenticated" || items.length === 0) return null;

  return (
    <div className="sticky top-16 z-40 border-b border-line bg-bg/60 backdrop-blur-md" data-section="watchlist-strip">
      <div className="mx-auto flex max-w-[1320px] items-center gap-2 overflow-x-auto px-6 py-2">
        <span className="shrink-0 text-[12px] font-semibold text-muted">관심종목</span>
        {items.map((w) => {
          const change = changePcts[w.stock.stock_code];
          const up = change != null && change.pct >= 0;
          return (
            <Link
              key={w.stock.stock_code}
              href={`/report/${encodeURIComponent(w.stock.stock_code)}`}
              className="shrink-0 rounded-full border border-line px-3 py-1 text-[12.5px] font-medium text-navy-soft hover:border-navy hover:text-navy"
            >
              {w.stock.stock_name} <span className="text-muted">{w.stock.stock_code}</span>
              {change && (
                <span className={`ml-1.5 font-bold ${up ? "text-[#16a34a]" : "text-[#dc2626]"}`}>
                  {up ? "▲" : "▼"} {Math.abs(change.pct).toFixed(2)}%
                </span>
              )}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
