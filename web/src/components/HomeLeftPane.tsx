"use client";

import { dateTimeKST } from "@/lib/format";
import { useHomeStore } from "@/stores/homeStore";

// FR-2/FR-3 좌 pane = 종목 칩 + 전역 뉴스 피드(#798). 종목 검색은 헤더로 승격(HeaderStockSearch).
// 종목 칩 또는 뉴스 항목을 고르면 우 pane(선택 종목 상세)이 갱신된다.
export function HomeLeftPane() {
  const feed = useHomeStore((s) => s.feed);
  const chips = useHomeStore((s) => s.chips);
  const loading = useHomeStore((s) => s.loading);
  const selectedCode = useHomeStore((s) => s.selectedCode);
  const select = useHomeStore((s) => s.select);

  return (
    <aside className="flex min-h-0 flex-col gap-4">
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
