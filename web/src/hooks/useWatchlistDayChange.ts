"use client";

import { useEffect, useState } from "react";
import { getReportPrices, type WatchlistItem } from "@/lib/apiClient";

// 관심종목 칩/밴드에 붙는 "당일 등락률". 아코디언 캔들 차트와 같은 소스(getReportPrices day)를
// 써서 차트에 표시되는 값과 정확히 일치시킨다(실 Yahoo 지연 시세, is_demo=false).
// 여러 컴포넌트(밴드·상단 스트립)가 동시에 마운트돼도 코드별 요청을 1회로 합치기 위해 모듈 캐시.

export type DayChange = { pct: number } | null;

const cache = new Map<string, Promise<DayChange>>();

function fetchChange(code: string): Promise<DayChange> {
  const hit = cache.get(code);
  if (hit) return hit;
  const p = getReportPrices(code, "day")
    .then((d): DayChange => (typeof d.change_pct === "number" ? { pct: d.change_pct } : null))
    .catch((): DayChange => null);
  cache.set(code, p);
  return p;
}

export function useWatchlistDayChange(items: WatchlistItem[]): Record<string, DayChange> {
  const [byCode, setByCode] = useState<Record<string, DayChange>>({});
  const codes = items.map((w) => w.stock.stock_code).join(",");

  useEffect(() => {
    if (items.length === 0) return;
    let alive = true;
    void Promise.all(
      items.map(async (w) => [w.stock.stock_code, await fetchChange(w.stock.stock_code)] as const),
    ).then((entries) => {
      if (alive) setByCode(Object.fromEntries(entries));
    });
    return () => {
      alive = false;
    };
    // codes 문자열로 목록 변경만 감지(참조 변경 무시).
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [codes]);

  return byCode;
}
