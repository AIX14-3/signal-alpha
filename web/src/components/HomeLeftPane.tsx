"use client";

import { StockLogo } from "@/components/StockLogo";
import { dateShortKST } from "@/lib/format";
import { useHomeStore } from "@/stores/homeStore";

// 좌 pane(대시보드 메인) = 실시간 뉴스 "트랜잭션 테이블"(레퍼런스 Transaction History 오마주).
// NewsSummary KPI(뉴스건수·종목수)는 상단 히어로(NewsSummaryBanner)가 이미 보여주므로 여기서는 생략.
// 종목 검색은 헤더(HeaderStockSearch)로 승격. 뉴스 행을 고르면 우 pane 스탯 레일이 갱신된다.
export function HomeLeftPane() {
  const feed = useHomeStore((s) => s.feed);
  const loading = useHomeStore((s) => s.loading);
  const selectedCode = useHomeStore((s) => s.selectedCode);
  const select = useHomeStore((s) => s.select);

  return (
    <div className="flex min-h-0 flex-col gap-5">
      {/* 실시간 뉴스 테이블 — flex-1 로 우측 분석 카드와 하단 높이를 맞춘다(그리드가 두 열을 같은
          높이로 늘리므로, 카드가 flex-1 로 그 높이를 채워야 바닥이 정렬된다). */}
      <div className="glass-card flex flex-1 flex-col overflow-hidden">
        <div className="flex items-center justify-between px-4 py-3">
          <div className="text-[14px] font-bold text-navy">뉴스 피드</div>
        </div>
        {/* 헤더·행이 같은 고정 폭 그리드를 써야 열이 정렬된다(auto 는 컨테이너마다 폭이 달라짐). */}
        <div className="grid grid-cols-[minmax(0,1fr)_6rem_2.75rem] items-center gap-x-3 border-y border-line bg-white px-4 py-2 text-[10.5px] font-bold uppercase tracking-wider text-muted">
          <span>뉴스</span>
          <span className="text-center">종목</span>
          <span className="text-right">날짜</span>
        </div>
        {loading ? (
          <ul className="animate-pulse space-y-3 p-4">
            {[0, 1, 2, 3, 4].map((i) => (
              <li key={i} className="h-9 rounded-[8px] bg-surface-2" />
            ))}
          </ul>
        ) : feed.length === 0 ? (
          <p className="flex flex-1 items-center justify-center p-8 text-center text-[13px] text-muted">
            아직 수집된 뉴스가 없습니다.
          </p>
        ) : (
          <ul className="min-h-0 flex-1 divide-y divide-line overflow-y-auto">
            {feed.slice(0, 14).map((n, i) => (
              <li key={`${n.stock_code}-${i}`}>
                <button
                  type="button"
                  onClick={() => n.stock_code && void select(n.stock_code)}
                  className={`grid w-full grid-cols-[minmax(0,1fr)_6rem_2.75rem] items-center gap-x-3 px-4 py-3 text-left transition hover:bg-surface-2 ${
                    selectedCode === n.stock_code ? "bg-surface-2" : ""
                  }`}
                >
                  <span className="min-w-0">
                    {/* 뉴스 제목 — 1줄(넘치면 말줄임). */}
                    <span className="block truncate text-[13.5px] font-medium text-navy-soft">
                      {n.title}
                    </span>
                    {n.press && <span className="mt-0.5 block text-[11px] text-muted">{n.press}</span>}
                  </span>
                  <span
                    className="pill min-w-0 max-w-full justify-self-end gap-1.5 border border-line bg-white text-[11.5px] font-semibold text-navy-soft"
                    style={{ padding: "3px 10px 3px 4px" }}
                  >
                    {n.stock_code && (
                      <StockLogo code={n.stock_code} name={n.stock_name} size={16} />
                    )}
                    <span className="truncate">{n.stock_name ?? n.stock_code}</span>
                  </span>
                  <span className="justify-self-end whitespace-nowrap text-[11px] text-muted">
                    {dateShortKST(n.published_at)}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}
